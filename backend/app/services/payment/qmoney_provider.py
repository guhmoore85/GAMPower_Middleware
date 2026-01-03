"""
QMoney Payment Provider

MOCK IMPLEMENTATION - Replace with real QMoney API integration.

TODO: Replace mock with actual QMoney API calls.

CRITICAL: All operations are logged loudly.
"""

from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.core.exceptions import PaymentProviderError
from app.core.logging import get_logger
from app.services.payment.base import (
    PaymentProvider,
    PaymentResult,
    PaymentStatus,
    RefundResult,
)

logger = get_logger(__name__)


class QMoneyPaymentProvider(PaymentProvider):
    """
    QMoney payment provider.

    MOCK IMPLEMENTATION:
    - Simulates successful payments
    - Logs all operations
    - Returns mock transaction IDs

    TODO: Integrate with real QMoney API
    """

    PROVIDER_NAME = "qmoney"

    async def initialize_payment(
        self,
        amount: Decimal,
        currency: str,
        customer_id: UUID,
        *,
        metadata: Optional[dict] = None,
        callback_url: Optional[str] = None,
    ) -> PaymentResult:
        """
        Initialize QMoney payment.

        MOCK: Returns mock transaction ID and payment URL.
        TODO: Replace with actual QMoney API call.
        """
        self._log_request(
            "initialize_payment",
            amount=float(amount),
            currency=currency,
            customer_id=str(customer_id),
            has_metadata=bool(metadata),
        )

        # TODO: Replace with real QMoney API call

        logger.warning(
            "MOCK: QMoney payment initialization - using simulated response",
            amount=float(amount),
            currency=currency,
        )

        try:
            mock_transaction_id = f"qm_txn_{uuid4().hex[:16]}"
            mock_payment_url = f"https://pay.qmoney.com/checkout/{mock_transaction_id}"

            result = PaymentResult(
                success=True,
                transaction_id=mock_transaction_id,
                status=PaymentStatus.PENDING,
                payment_url=mock_payment_url,
                provider_response={
                    "mock": True,
                    "message": "MOCK RESPONSE - Replace with real QMoney integration",
                },
            )

            self._log_response(
                "initialize_payment",
                success=True,
                transaction_id=mock_transaction_id,
            )

            return result

        except Exception as e:
            self._log_error("initialize_payment", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="initialize_payment",
                transaction_id=None,
                original_error=e,
            )

    async def verify_webhook(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Verify QMoney webhook signature.

        MOCK: Accepts all signatures for testing.
        TODO: Implement real signature verification.
        """
        self._log_request(
            "verify_webhook",
            payload_size=len(payload),
            signature_length=len(signature) if signature else 0,
        )

        logger.warning(
            "MOCK: QMoney webhook verification - accepting all signatures",
            signature_provided=bool(signature),
        )

        is_valid = True

        self._log_response(
            "verify_webhook",
            success=is_valid,
        )

        return is_valid

    async def get_transaction_status(
        self,
        transaction_id: str,
    ) -> PaymentStatus:
        """
        Get QMoney transaction status.

        MOCK: Returns COMPLETED for all transactions.
        TODO: Replace with actual QMoney API call.
        """
        self._log_request(
            "get_transaction_status",
            transaction_id=transaction_id,
        )

        logger.warning(
            "MOCK: QMoney transaction status - returning COMPLETED",
            transaction_id=transaction_id,
        )

        status = PaymentStatus.COMPLETED

        self._log_response(
            "get_transaction_status",
            success=True,
            status=status.value,
        )

        return status

    async def refund_transaction(
        self,
        transaction_id: str,
        *,
        amount: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> RefundResult:
        """
        Refund QMoney transaction.

        MOCK: Returns successful refund.
        TODO: Replace with actual QMoney API call.
        """
        self._log_request(
            "refund_transaction",
            transaction_id=transaction_id,
            amount=float(amount) if amount else "full",
            reason=reason,
        )

        logger.warning(
            "MOCK: QMoney refund - returning success",
            transaction_id=transaction_id,
        )

        try:
            mock_refund_id = f"qm_ref_{uuid4().hex[:16]}"

            result = RefundResult(
                success=True,
                refund_id=mock_refund_id,
                amount=amount,
                provider_response={
                    "mock": True,
                    "message": "MOCK RESPONSE",
                },
            )

            self._log_response(
                "refund_transaction",
                success=True,
                refund_id=mock_refund_id,
            )

            return result

        except Exception as e:
            self._log_error("refund_transaction", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="refund_transaction",
                transaction_id=transaction_id,
                original_error=e,
            )

    def parse_webhook_payload(
        self,
        payload: dict,
    ) -> dict:
        """
        Parse QMoney webhook payload.

        QMoney webhook format (example):
        {
            "notification_type": "PAYMENT_COMPLETED",
            "transaction_reference": "qm_txn_xxx",
            "amount": 100.00,
            "currency": "XOF",
            "status": "SUCCESS",
            "timestamp": "2024-01-01T12:00:00Z"
        }
        """
        self._log_request(
            "parse_webhook_payload",
            notification_type=payload.get("notification_type"),
        )

        try:
            parsed = {
                "event_type": payload.get("notification_type"),
                "transaction_id": payload.get("transaction_reference"),
                "status": self._map_status(payload.get("status")),
                "amount": Decimal(str(payload.get("amount", 0))),
                "currency": payload.get("currency"),
                "metadata": payload.get("metadata", {}),
            }

            logger.debug(
                "QMoney webhook parsed",
                transaction_id=parsed["transaction_id"],
                status=parsed["status"],
            )

            return parsed

        except Exception as e:
            logger.error(
                "Failed to parse QMoney webhook",
                error=str(e),
                payload_keys=list(payload.keys()),
            )
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="parse_webhook",
                original_error=e,
            )

    def _map_status(self, provider_status: Optional[str]) -> str:
        """Map QMoney status to our PaymentStatus."""
        status_map = {
            "pending": PaymentStatus.PENDING.value,
            "success": PaymentStatus.COMPLETED.value,
            "completed": PaymentStatus.COMPLETED.value,
            "failed": PaymentStatus.FAILED.value,
            "failure": PaymentStatus.FAILED.value,
            "cancelled": PaymentStatus.FAILED.value,
            "refunded": PaymentStatus.REFUNDED.value,
        }
        return status_map.get(
            (provider_status or "").lower(),
            PaymentStatus.PENDING.value,
        )
