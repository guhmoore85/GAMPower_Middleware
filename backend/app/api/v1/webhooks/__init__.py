"""
Webhook API Endpoints

Handles payment provider webhooks:
- Wave
- QMoney
- Apple Pay

CRITICAL: All webhooks are logged and idempotent.
Always returns 200 OK to prevent provider retries.
"""

import traceback
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import DbSessionDep, RequestIdDep
from app.core.logging import audit_logger, get_logger
from app.models.transaction import PaymentProvider, Transaction, TransactionStatus
from app.schemas.transaction import (
    ApplePayWebhookPayload,
    QMoneyWebhookPayload,
    WaveWebhookPayload,
)
from app.services.payment.factory import get_payment_provider
from app.services.trigger_service import TriggerService

router = APIRouter()
logger = get_logger(__name__)


async def process_webhook(
    provider: str,
    db: AsyncSession,
    parsed_data: dict,
    request_id: str,
) -> dict:
    """
    Common webhook processing logic.

    1. Find transaction by provider_transaction_id
    2. Update transaction status
    3. Trigger device activation if completed
    4. Return processing result

    LOUD: Logs every step.
    """
    transaction_id = parsed_data.get("transaction_id")
    new_status = parsed_data.get("status")

    logger.info(
        "Processing webhook",
        provider=provider,
        transaction_id=transaction_id,
        new_status=new_status,
        request_id=request_id,
    )

    # Find transaction
    result = await db.execute(
        select(Transaction).where(
            Transaction.provider_transaction_id == transaction_id,
            Transaction.payment_provider == PaymentProvider(provider),
        )
    )
    transaction = result.scalar_one_or_none()

    if not transaction:
        logger.warning(
            "Webhook for unknown transaction",
            provider=provider,
            transaction_id=transaction_id,
            request_id=request_id,
        )
        return {
            "processed": False,
            "reason": "Transaction not found",
        }

    # Check if already processed (idempotency)
    if transaction.status.value == new_status:
        logger.info(
            "Webhook already processed (idempotent)",
            provider=provider,
            transaction_id=transaction_id,
            status=new_status,
            request_id=request_id,
        )
        return {
            "processed": True,
            "reason": "Already processed",
        }

    # Update transaction status
    try:
        old_status = transaction.status

        if new_status == "completed":
            transaction.transition_status(
                TransactionStatus.COMPLETED,
                reason=f"Webhook from {provider}",
            )
        elif new_status == "failed":
            transaction.transition_status(
                TransactionStatus.FAILED,
                reason=f"Webhook from {provider}",
                failure_reason=parsed_data.get("error_message", "Payment failed"),
            )
        elif new_status == "refunded":
            transaction.transition_status(
                TransactionStatus.REFUNDED,
                reason=f"Webhook from {provider}",
            )

        await db.commit()

        logger.info(
            "Transaction status updated via webhook",
            transaction_id=str(transaction.id),
            old_status=old_status.value,
            new_status=transaction.status.value,
            request_id=request_id,
        )

        # Audit log
        audit_logger.log_event(
            event_type="transaction",
            action="webhook_update",
            resource_type="transaction",
            resource_id=str(transaction.id),
            details={
                "provider": provider,
                "old_status": old_status.value,
                "new_status": transaction.status.value,
            },
        )

        # Trigger device activation if completed
        if transaction.status == TransactionStatus.COMPLETED and transaction.device_id:
            try:
                trigger_service = TriggerService(db)
                activation = await trigger_service.process_successful_payment(
                    transaction.id
                )
                if activation:
                    logger.info(
                        "Device activated after webhook",
                        transaction_id=str(transaction.id),
                        activation_id=str(activation.id),
                        request_id=request_id,
                    )
            except Exception as e:
                # Log but don't fail webhook
                logger.error(
                    "Device activation failed after webhook",
                    transaction_id=str(transaction.id),
                    error=str(e),
                    request_id=request_id,
                )

        return {
            "processed": True,
            "transaction_id": str(transaction.id),
            "new_status": transaction.status.value,
        }

    except Exception as e:
        logger.error(
            "Webhook processing failed",
            provider=provider,
            transaction_id=transaction_id,
            error=str(e),
            stack_trace=traceback.format_exc(),
            request_id=request_id,
        )
        await db.rollback()
        return {
            "processed": False,
            "reason": str(e),
        }


# =============================================================================
# WAVE WEBHOOK
# =============================================================================


@router.post("/wave")
async def wave_webhook(
    request: Request,
    request_id: RequestIdDep,
    db: DbSessionDep,
    wave_signature: str = Header(None, alias="Wave-Signature"),
):
    """
    Wave payment webhook handler.

    Wave sends the signature in the Wave-Signature header:
        Wave-Signature: t=<timestamp>,v1=<signature>

    Always returns 200 OK to prevent retries.
    Errors are logged but not returned.
    """
    logger.info(
        "Wave webhook received",
        signature_present=bool(wave_signature),
        request_id=request_id,
    )

    try:
        # Get raw body for signature verification (before parsing JSON)
        body = await request.body()

        # Verify signature using Wave's signing-secret strategy
        provider = get_payment_provider("wave")
        is_valid = await provider.verify_webhook(
            payload=body,
            signature=wave_signature or "",
        )

        if not is_valid:
            logger.warning(
                "Wave webhook signature verification failed",
                request_id=request_id,
            )
            # Still return 200 but log the failure

        # Parse payload
        payload = await request.json()
        parsed = provider.parse_webhook_payload(payload)

        # Process webhook
        result = await process_webhook("wave", db, parsed, request_id)

        logger.info(
            "Wave webhook processed",
            result=result,
            request_id=request_id,
        )

        return {"status": "received", **result}

    except Exception as e:
        logger.error(
            "Wave webhook error",
            error=str(e),
            stack_trace=traceback.format_exc(),
            request_id=request_id,
        )
        # Always return 200 OK
        return {"status": "error", "message": "Webhook processing failed"}


# =============================================================================
# QMONEY WEBHOOK
# =============================================================================


@router.post("/qmoney")
async def qmoney_webhook(
    request: Request,
    request_id: RequestIdDep,
    db: DbSessionDep,
    x_qmoney_signature: str = Header(None, alias="X-QMoney-Signature"),
):
    """
    QMoney payment webhook handler.

    Always returns 200 OK to prevent retries.
    """
    logger.info(
        "QMoney webhook received",
        signature_present=bool(x_qmoney_signature),
        request_id=request_id,
    )

    try:
        body = await request.body()

        # Verify signature
        provider = get_payment_provider("qmoney")
        is_valid = await provider.verify_webhook(
            payload=body,
            signature=x_qmoney_signature or "",
        )

        if not is_valid:
            logger.warning(
                "QMoney webhook signature verification failed",
                request_id=request_id,
            )

        # Parse payload
        payload = await request.json()
        parsed = provider.parse_webhook_payload(payload)

        # Process webhook
        result = await process_webhook("qmoney", db, parsed, request_id)

        logger.info(
            "QMoney webhook processed",
            result=result,
            request_id=request_id,
        )

        return {"status": "received", **result}

    except Exception as e:
        logger.error(
            "QMoney webhook error",
            error=str(e),
            stack_trace=traceback.format_exc(),
            request_id=request_id,
        )
        return {"status": "error", "message": "Webhook processing failed"}


# =============================================================================
# APPLE PAY WEBHOOK
# =============================================================================


@router.post("/applepay")
async def apple_pay_webhook(
    request: Request,
    request_id: RequestIdDep,
    db: DbSessionDep,
    x_apple_signature: str = Header(None, alias="X-Apple-Signature"),
):
    """
    Apple Pay notification handler.

    Always returns 200 OK to prevent retries.
    """
    logger.info(
        "Apple Pay webhook received",
        signature_present=bool(x_apple_signature),
        request_id=request_id,
    )

    try:
        body = await request.body()

        # Verify signature
        provider = get_payment_provider("apple_pay")
        is_valid = await provider.verify_webhook(
            payload=body,
            signature=x_apple_signature or "",
        )

        if not is_valid:
            logger.warning(
                "Apple Pay webhook signature verification failed",
                request_id=request_id,
            )

        # Parse payload
        payload = await request.json()
        parsed = provider.parse_webhook_payload(payload)

        # Process webhook
        result = await process_webhook("apple_pay", db, parsed, request_id)

        logger.info(
            "Apple Pay webhook processed",
            result=result,
            request_id=request_id,
        )

        return {"status": "received", **result}

    except Exception as e:
        logger.error(
            "Apple Pay webhook error",
            error=str(e),
            stack_trace=traceback.format_exc(),
            request_id=request_id,
        )
        return {"status": "error", "message": "Webhook processing failed"}
