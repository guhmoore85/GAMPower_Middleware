"""
Payment Trigger Service

Evaluates usage-based and time-based triggers to initiate payments.

CRITICAL: All trigger evaluations are logged with full context.
Errors are handled per-device to prevent batch failures.
"""

import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    CustomerNotFoundError,
    DeviceNotFoundError,
    TriggerEvaluationError,
)
from app.core.logging import audit_logger, get_logger
from app.core.security import generate_idempotency_key
from app.models.customer import Customer
from app.models.device import Device, DeviceStatus
from app.models.device_activation import ActivationType, DeviceActivation
from app.models.device_metric import DeviceMetric, MetricType
from app.models.payment_trigger import PaymentTrigger, TriggerType
from app.models.transaction import PaymentProvider, Transaction, TransactionStatus
from app.services.openpaygo_service import OpenPAYGOService
from app.services.payment.factory import get_payment_provider

logger = get_logger(__name__)


class TriggerEvaluationResult:
    """Result of trigger evaluation."""

    def __init__(
        self,
        device_id: UUID,
        trigger_id: UUID,
        trigger_type: TriggerType,
        triggered: bool,
        reason: str,
        *,
        current_value: Optional[Decimal] = None,
        threshold: Optional[Decimal] = None,
        action_taken: Optional[str] = None,
        payment_created: bool = False,
        transaction_id: Optional[UUID] = None,
        error: Optional[str] = None,
    ):
        self.device_id = device_id
        self.trigger_id = trigger_id
        self.trigger_type = trigger_type
        self.triggered = triggered
        self.reason = reason
        self.current_value = current_value
        self.threshold = threshold
        self.action_taken = action_taken
        self.payment_created = payment_created
        self.transaction_id = transaction_id
        self.error = error

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "device_id": str(self.device_id),
            "trigger_id": str(self.trigger_id),
            "trigger_type": self.trigger_type.value,
            "triggered": self.triggered,
            "reason": self.reason,
            "current_value": float(self.current_value) if self.current_value else None,
            "threshold": float(self.threshold) if self.threshold else None,
            "action_taken": self.action_taken,
            "payment_created": self.payment_created,
            "transaction_id": str(self.transaction_id) if self.transaction_id else None,
            "error": self.error,
        }


class TriggerService:
    """
    Payment trigger evaluation service.

    Handles:
    - Usage-based triggers (threshold exceeded)
    - Time-based triggers (periodic payments)
    - Manual triggers

    LOUD ERROR HANDLING:
    - All evaluations logged with results
    - Errors per-device don't fail entire batch
    - Full stack traces on errors
    """

    def __init__(self, db: AsyncSession):
        """Initialize trigger service."""
        self.db = db
        self.openpaygo_service = OpenPAYGOService(db)
        logger.debug("Trigger service initialized")

    async def evaluate_triggers_for_device(
        self,
        device_id: UUID,
        *,
        trigger_types: Optional[list[TriggerType]] = None,
        dry_run: bool = False,
    ) -> list[TriggerEvaluationResult]:
        """
        Evaluate all active triggers for a device.

        Args:
            device_id: Device UUID
            trigger_types: Specific types to evaluate (all if None)
            dry_run: If True, don't create payments

        Returns:
            List of evaluation results

        LOUD: Logs each trigger evaluation with result.
        """
        results = []

        logger.info(
            "Evaluating triggers for device",
            device_id=str(device_id),
            trigger_types=[t.value for t in trigger_types] if trigger_types else "all",
            dry_run=dry_run,
        )

        try:
            # Get device
            device = await self._get_device(device_id)
            if not device:
                logger.error(
                    "Device not found for trigger evaluation",
                    device_id=str(device_id),
                )
                raise DeviceNotFoundError(device_id)

            # Check device status
            if device.status != DeviceStatus.ACTIVE:
                logger.info(
                    "Skipping triggers for non-active device",
                    device_id=str(device_id),
                    status=device.status.value,
                )
                return results

            # Get active triggers
            triggers = await self._get_active_triggers(device_id, trigger_types)

            if not triggers:
                logger.debug(
                    "No active triggers for device",
                    device_id=str(device_id),
                )
                return results

            # Evaluate each trigger
            for trigger in triggers:
                try:
                    result = await self._evaluate_single_trigger(
                        device, trigger, dry_run=dry_run
                    )
                    results.append(result)

                except Exception as e:
                    logger.error(
                        "Trigger evaluation failed",
                        device_id=str(device_id),
                        trigger_id=str(trigger.id),
                        trigger_type=trigger.trigger_type.value,
                        error=str(e),
                        stack_trace=traceback.format_exc(),
                    )
                    results.append(
                        TriggerEvaluationResult(
                            device_id=device_id,
                            trigger_id=trigger.id,
                            trigger_type=trigger.trigger_type,
                            triggered=False,
                            reason="Evaluation error",
                            error=str(e),
                        )
                    )

            logger.info(
                "Trigger evaluation completed",
                device_id=str(device_id),
                total_triggers=len(triggers),
                triggered_count=sum(1 for r in results if r.triggered),
                error_count=sum(1 for r in results if r.error),
            )

            return results

        except DeviceNotFoundError:
            raise
        except Exception as e:
            logger.error(
                "Failed to evaluate triggers for device",
                device_id=str(device_id),
                error=str(e),
                stack_trace=traceback.format_exc(),
            )
            raise TriggerEvaluationError(
                device_id=device_id,
                trigger_type="all",
                reason=str(e),
                original_error=e,
            )

    async def _evaluate_single_trigger(
        self,
        device: Device,
        trigger: PaymentTrigger,
        *,
        dry_run: bool = False,
    ) -> TriggerEvaluationResult:
        """Evaluate a single trigger."""
        logger.debug(
            "Evaluating trigger",
            device_id=str(device.id),
            trigger_id=str(trigger.id),
            trigger_type=trigger.trigger_type.value,
        )

        if trigger.trigger_type == TriggerType.USAGE_THRESHOLD:
            return await self._evaluate_usage_trigger(device, trigger, dry_run=dry_run)
        elif trigger.trigger_type == TriggerType.TIME_BASED:
            return await self._evaluate_time_trigger(device, trigger, dry_run=dry_run)
        elif trigger.trigger_type == TriggerType.MANUAL:
            # Manual triggers are only fired explicitly
            return TriggerEvaluationResult(
                device_id=device.id,
                trigger_id=trigger.id,
                trigger_type=trigger.trigger_type,
                triggered=False,
                reason="Manual trigger - not evaluated automatically",
            )
        else:
            return TriggerEvaluationResult(
                device_id=device.id,
                trigger_id=trigger.id,
                trigger_type=trigger.trigger_type,
                triggered=False,
                reason=f"Unknown trigger type: {trigger.trigger_type}",
            )

    async def _evaluate_usage_trigger(
        self,
        device: Device,
        trigger: PaymentTrigger,
        *,
        dry_run: bool = False,
    ) -> TriggerEvaluationResult:
        """
        Evaluate usage threshold trigger.

        Checks if device usage exceeds threshold since last trigger.
        """
        config = trigger.config
        metric_type = MetricType(config.get("metric_type"))
        threshold = Decimal(str(config.get("threshold", 0)))

        logger.debug(
            "Evaluating usage threshold trigger",
            device_id=str(device.id),
            trigger_id=str(trigger.id),
            metric_type=metric_type.value,
            threshold=float(threshold),
        )

        # Get usage since last triggered (or all time if never triggered)
        since = trigger.last_triggered_at or device.created_at

        query = select(func.sum(DeviceMetric.value)).where(
            DeviceMetric.device_id == device.id,
            DeviceMetric.metric_type == metric_type,
            DeviceMetric.timestamp >= since,
        )

        result = await self.db.execute(query)
        current_usage = result.scalar() or Decimal("0")

        logger.debug(
            "Current usage calculated",
            device_id=str(device.id),
            metric_type=metric_type.value,
            current_usage=float(current_usage),
            threshold=float(threshold),
            since=since.isoformat(),
        )

        if current_usage >= threshold:
            logger.info(
                "Usage threshold exceeded",
                device_id=str(device.id),
                trigger_id=str(trigger.id),
                current_usage=float(current_usage),
                threshold=float(threshold),
            )

            action_taken = None
            payment_created = False
            transaction_id = None

            if not dry_run:
                # Create payment request
                try:
                    transaction = await self._create_payment_request(
                        device, trigger
                    )
                    if transaction:
                        action_taken = "Payment request created"
                        payment_created = True
                        transaction_id = transaction.id

                        # Mark trigger as triggered
                        trigger.mark_triggered()
                except Exception as e:
                    logger.error(
                        "Failed to create payment for trigger",
                        device_id=str(device.id),
                        trigger_id=str(trigger.id),
                        error=str(e),
                    )
                    action_taken = f"Payment creation failed: {e}"
            else:
                action_taken = "DRY RUN - would create payment"

            return TriggerEvaluationResult(
                device_id=device.id,
                trigger_id=trigger.id,
                trigger_type=trigger.trigger_type,
                triggered=True,
                reason=f"Usage {current_usage} >= threshold {threshold}",
                current_value=current_usage,
                threshold=threshold,
                action_taken=action_taken,
                payment_created=payment_created,
                transaction_id=transaction_id,
            )

        return TriggerEvaluationResult(
            device_id=device.id,
            trigger_id=trigger.id,
            trigger_type=trigger.trigger_type,
            triggered=False,
            reason=f"Usage {current_usage} < threshold {threshold}",
            current_value=current_usage,
            threshold=threshold,
        )

    async def _evaluate_time_trigger(
        self,
        device: Device,
        trigger: PaymentTrigger,
        *,
        dry_run: bool = False,
    ) -> TriggerEvaluationResult:
        """
        Evaluate time-based trigger.

        Checks if enough time has passed since last trigger.
        """
        config = trigger.config
        interval_days = config.get("interval_days", 30)

        logger.debug(
            "Evaluating time-based trigger",
            device_id=str(device.id),
            trigger_id=str(trigger.id),
            interval_days=interval_days,
        )

        # Calculate next trigger time
        last_triggered = trigger.last_triggered_at or trigger.created_at
        next_trigger = last_triggered + timedelta(days=interval_days)
        now = datetime.now(timezone.utc)

        logger.debug(
            "Time trigger check",
            device_id=str(device.id),
            last_triggered=last_triggered.isoformat(),
            next_trigger=next_trigger.isoformat(),
            now=now.isoformat(),
        )

        if now >= next_trigger:
            logger.info(
                "Time trigger activated",
                device_id=str(device.id),
                trigger_id=str(trigger.id),
                days_since_last=int((now - last_triggered).days),
            )

            action_taken = None
            payment_created = False
            transaction_id = None

            if not dry_run:
                try:
                    transaction = await self._create_payment_request(
                        device, trigger
                    )
                    if transaction:
                        action_taken = "Payment request created"
                        payment_created = True
                        transaction_id = transaction.id
                        trigger.mark_triggered()
                except Exception as e:
                    logger.error(
                        "Failed to create payment for trigger",
                        device_id=str(device.id),
                        trigger_id=str(trigger.id),
                        error=str(e),
                    )
                    action_taken = f"Payment creation failed: {e}"
            else:
                action_taken = "DRY RUN - would create payment"

            return TriggerEvaluationResult(
                device_id=device.id,
                trigger_id=trigger.id,
                trigger_type=trigger.trigger_type,
                triggered=True,
                reason=f"Time trigger: {interval_days} days elapsed",
                action_taken=action_taken,
                payment_created=payment_created,
                transaction_id=transaction_id,
            )

        days_remaining = (next_trigger - now).days

        return TriggerEvaluationResult(
            device_id=device.id,
            trigger_id=trigger.id,
            trigger_type=trigger.trigger_type,
            triggered=False,
            reason=f"Next trigger in {days_remaining} days",
        )

    async def _create_payment_request(
        self,
        device: Device,
        trigger: PaymentTrigger,
    ) -> Optional[Transaction]:
        """
        Create payment request after trigger evaluation.

        LOUD: Logs all steps of payment creation.
        """
        logger.info(
            "Creating payment request from trigger",
            device_id=str(device.id),
            trigger_id=str(trigger.id),
        )

        # Get customer
        if not device.customer_id:
            logger.warning(
                "Device has no customer - cannot create payment",
                device_id=str(device.id),
            )
            return None

        customer = await self._get_customer(device.customer_id)
        if not customer:
            logger.error(
                "Customer not found for device",
                device_id=str(device.id),
                customer_id=str(device.customer_id),
            )
            return None

        # Get amount and currency from trigger config or defaults
        config = trigger.config
        amount = Decimal(str(config.get("amount", 10)))  # Default $10
        currency = config.get("currency", "USD")

        # Generate idempotency key
        idempotency_key = generate_idempotency_key(
            prefix="trigger",
            str(trigger.id),
            str(device.id),
        )

        # Get payment provider
        provider = get_payment_provider(customer.payment_provider)

        # Initialize payment
        payment_result = await provider.initialize_payment(
            amount=amount,
            currency=currency,
            customer_id=customer.id,
            metadata={
                "device_id": str(device.id),
                "trigger_id": str(trigger.id),
                "trigger_type": trigger.trigger_type.value,
            },
        )

        if not payment_result.success:
            logger.error(
                "Payment initialization failed",
                device_id=str(device.id),
                error_code=payment_result.error_code,
                error_message=payment_result.error_message,
            )
            return None

        # Create transaction record
        transaction = Transaction(
            customer_id=customer.id,
            device_id=device.id,
            payment_provider=customer.payment_provider,
            provider_transaction_id=payment_result.transaction_id,
            amount=amount,
            currency=currency,
            status=TransactionStatus.PENDING,
            idempotency_key=idempotency_key,
            metadata_={
                "trigger_id": str(trigger.id),
                "payment_url": payment_result.payment_url,
            },
        )

        self.db.add(transaction)
        await self.db.flush()

        logger.info(
            "Payment request created",
            transaction_id=str(transaction.id),
            device_id=str(device.id),
            trigger_id=str(trigger.id),
            amount=float(amount),
            currency=currency,
        )

        # Audit log
        audit_logger.log_event(
            event_type="transaction",
            action="trigger_payment",
            resource_type="transaction",
            resource_id=str(transaction.id),
            details={
                "device_id": str(device.id),
                "trigger_id": str(trigger.id),
                "trigger_type": trigger.trigger_type.value,
                "amount": float(amount),
                "currency": currency,
            },
        )

        return transaction

    async def process_successful_payment(
        self,
        transaction_id: UUID,
    ) -> Optional[DeviceActivation]:
        """
        Process successful payment - activate device.

        Called by webhook handlers after payment completion.

        LOUD: Logs entire activation flow.
        """
        logger.info(
            "Processing successful payment",
            transaction_id=str(transaction_id),
        )

        # Get transaction
        result = await self.db.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        transaction = result.scalar_one_or_none()

        if not transaction:
            logger.error(
                "Transaction not found",
                transaction_id=str(transaction_id),
            )
            return None

        # Verify status
        if transaction.status != TransactionStatus.COMPLETED:
            logger.warning(
                "Transaction not completed",
                transaction_id=str(transaction_id),
                status=transaction.status.value,
            )
            return None

        # Check if already activated
        existing_activation = await self.db.execute(
            select(DeviceActivation).where(
                DeviceActivation.transaction_id == transaction_id
            )
        )
        if existing_activation.scalar_one_or_none():
            logger.info(
                "Transaction already has activation",
                transaction_id=str(transaction_id),
            )
            return None

        if not transaction.device_id:
            logger.warning(
                "Transaction has no device",
                transaction_id=str(transaction_id),
            )
            return None

        try:
            # Generate token and activate device
            token, expiry = await self.openpaygo_service.generate_token(
                device_id=transaction.device_id,
                days_valid=settings.openpaygo_default_token_days,
            )

            activation = await self.openpaygo_service.activate_device(
                device_id=transaction.device_id,
                token=token,
                activation_type=ActivationType.PAYMENT,
                transaction_id=transaction_id,
                days_valid=settings.openpaygo_default_token_days,
            )

            logger.info(
                "Device activated after payment",
                transaction_id=str(transaction_id),
                device_id=str(transaction.device_id),
                activation_id=str(activation.id),
            )

            return activation

        except Exception as e:
            logger.error(
                "Failed to activate device after payment",
                transaction_id=str(transaction_id),
                device_id=str(transaction.device_id),
                error=str(e),
                stack_trace=traceback.format_exc(),
            )
            return None

    async def evaluate_all_triggers(self) -> dict:
        """
        Evaluate triggers for all active devices.

        Used by background scheduler.

        LOUD: Logs summary of evaluation.
        """
        logger.info("Starting trigger evaluation for all devices")

        summary = {
            "devices_checked": 0,
            "triggers_evaluated": 0,
            "triggers_fired": 0,
            "payments_created": 0,
            "errors": 0,
        }

        # Get all active devices with triggers
        result = await self.db.execute(
            select(Device.id).where(
                Device.status == DeviceStatus.ACTIVE
            )
        )
        device_ids = [row[0] for row in result.fetchall()]

        logger.info(
            "Found active devices for trigger evaluation",
            count=len(device_ids),
        )

        for device_id in device_ids:
            try:
                results = await self.evaluate_triggers_for_device(device_id)

                summary["devices_checked"] += 1
                summary["triggers_evaluated"] += len(results)
                summary["triggers_fired"] += sum(1 for r in results if r.triggered)
                summary["payments_created"] += sum(1 for r in results if r.payment_created)
                summary["errors"] += sum(1 for r in results if r.error)

            except Exception as e:
                logger.error(
                    "Failed to evaluate triggers for device",
                    device_id=str(device_id),
                    error=str(e),
                )
                summary["errors"] += 1

        logger.info(
            "Trigger evaluation completed",
            **summary,
        )

        return summary

    # Helper methods

    async def _get_device(self, device_id: UUID) -> Optional[Device]:
        """Get device by ID."""
        result = await self.db.execute(
            select(Device).where(Device.id == device_id)
        )
        return result.scalar_one_or_none()

    async def _get_customer(self, customer_id: UUID) -> Optional[Customer]:
        """Get customer by ID."""
        result = await self.db.execute(
            select(Customer).where(Customer.id == customer_id)
        )
        return result.scalar_one_or_none()

    async def _get_active_triggers(
        self,
        device_id: UUID,
        trigger_types: Optional[list[TriggerType]] = None,
    ) -> list[PaymentTrigger]:
        """Get active triggers for device."""
        query = select(PaymentTrigger).where(
            PaymentTrigger.device_id == device_id,
            PaymentTrigger.is_active == True,
        )

        if trigger_types:
            query = query.where(PaymentTrigger.trigger_type.in_(trigger_types))

        result = await self.db.execute(query)
        return list(result.scalars().all())
