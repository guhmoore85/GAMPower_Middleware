"""
Wave Payment Provider

MOCK IMPLEMENTATION - Replace with real Wave API integration.

TODO: Replace mock with actual Wave API calls:
- API Documentation: https://developers.wave.com
- Authentication: OAuth2 or API Key
- Endpoints: /v1/payments, /v1/transactions, etc.

CRITICAL: All operations are logged loudly.
"""

from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.core.exceptions import PaymentProviderError
from app.core.logging import get_logger
from app.core.security import verify_webhook_signature
from app.services.payment.base import (
    PaymentProvider,
    PaymentResult,
    PaymentStatus,
    RefundResult,
)

logger = get_logger(__name__)


class WavePaymentProvider(PaymentProvider):
    """
    Wave Money payment provider.

    MOCK IMPLEMENTATION:
    - Simulates successful payments
    - Logs all operations
    - Returns mock transaction IDs

    TODO: Integrate with real Wave API
    """

    PROVIDER_NAME = "wave"

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
        Initialize Wave payment.

        MOCK: Returns mock transaction ID and payment URL.
        TODO: Replace with actual Wave API call.
        """
        self._log_request(
            "initialize_payment",
            amount=float(amount),
            currency=currency,
            customer_id=str(customer_id),
            has_metadata=bool(metadata),
        )

        # TODO: Replace with real Wave API call
        # Example real implementation:
        # response = await httpx.AsyncClient().post(
        #     f"{self.api_url}/payments",
        #     headers={"Authorization": f"Bearer {self.api_key}"},
        #     json={
        #         "amount": str(amount),
        #         "currency": currency,
        #         "customer_reference": str(customer_id),
        #         "callback_url": callback_url,
        #         "metadata": metadata,
        #     },
        # )

        logger.warning(
            "MOCK: Wave payment initialization - using simulated response",
            amount=float(amount),
            currency=currency,
        )

        try:
            # Mock successful response
            mock_transaction_id = f"wave_txn_{uuid4().hex[:16]}"
            mock_payment_url = f"https://pay.wave.com/checkout/{mock_transaction_id}"

            result = PaymentResult(
                success=True,
                transaction_id=mock_transaction_id,
                status=PaymentStatus.PENDING,
                payment_url=mock_payment_url,
                provider_response={
                    "mock": True,
                    "message": "MOCK RESPONSE - Replace with real Wave integration",
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
        Verify Wave webhook signature.

        MOCK: Accepts all signatures for testing.
        TODO: Implement real signature verification.
        """
        self._log_request(
            "verify_webhook",
            payload_size=len(payload),
            signature_length=len(signature) if signature else 0,
        )

        # TODO: Replace with real Wave signature verification
        # Wave typically uses HMAC-SHA256
        # is_valid = verify_webhook_signature(
        #     payload=payload,
        #     signature=signature,
        #     secret=self.webhook_secret,
        #     provider=self.PROVIDER_NAME,
        # )

        logger.warning(
            "MOCK: Wave webhook verification - accepting all signatures",
            signature_provided=bool(signature),
        )

        # Mock: Accept all for testing
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
        Get Wave transaction status.

        MOCK: Returns COMPLETED for all transactions.
        TODO: Replace with actual Wave API call.
        """
        self._log_request(
            "get_transaction_status",
            transaction_id=transaction_id,
        )

        # TODO: Replace with real Wave API call
        # response = await httpx.AsyncClient().get(
        #     f"{self.api_url}/transactions/{transaction_id}",
        #     headers={"Authorization": f"Bearer {self.api_key}"},
        # )
        # status = response.json()["status"]

        logger.warning(
            "MOCK: Wave transaction status - returning COMPLETED",
            transaction_id=transaction_id,
        )

        # Mock: Always return completed
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
        Refund Wave transaction.

        MOCK: Returns successful refund.
        TODO: Replace with actual Wave API call.
        """
        self._log_request(
            "refund_transaction",
            transaction_id=transaction_id,
            amount=float(amount) if amount else "full",
            reason=reason,
        )

        # TODO: Replace with real Wave API call
        # response = await httpx.AsyncClient().post(
        #     f"{self.api_url}/transactions/{transaction_id}/refund",
        #     headers={"Authorization": f"Bearer {self.api_key}"},
        #     json={
        #         "amount": str(amount) if amount else None,
        #         "reason": reason,
        #     },
        # )

        logger.warning(
            "MOCK: Wave refund - returning success",
            transaction_id=transaction_id,
        )

        try:
            mock_refund_id = f"wave_ref_{uuid4().hex[:16]}"

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
        Parse Wave webhook payload.

        Wave webhook format (example):
        {
            "event": "payment.completed",
            "data": {
                "id": "wave_txn_xxx",
                "amount": "100.00",
                "currency": "XOF",
                "status": "completed",
                "metadata": {...}
            }
        }
        """
        self._log_request(
            "parse_webhook_payload",
            event_type=payload.get("event"),
        )

        try:
            data = payload.get("data", {})

            parsed = {
                "event_type": payload.get("event"),
                "transaction_id": data.get("id"),
                "status": self._map_status(data.get("status")),
                "amount": Decimal(data.get("amount", "0")),
                "currency": data.get("currency"),
                "metadata": data.get("metadata", {}),
            }

            logger.debug(
                "Wave webhook parsed",
                transaction_id=parsed["transaction_id"],
                status=parsed["status"],
            )

            return parsed

        except Exception as e:
            logger.error(
                "Failed to parse Wave webhook",
                error=str(e),
                payload_keys=list(payload.keys()),
            )
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="parse_webhook",
                original_error=e,
            )

    def _map_status(self, provider_status: Optional[str]) -> str:
        """Map Wave status to our PaymentStatus."""
        status_map = {
            "pending": PaymentStatus.PENDING.value,
            "completed": PaymentStatus.COMPLETED.value,
            "successful": PaymentStatus.COMPLETED.value,
            "failed": PaymentStatus.FAILED.value,
            "cancelled": PaymentStatus.FAILED.value,
            "refunded": PaymentStatus.REFUNDED.value,
        }
        return status_map.get(
            (provider_status or "").lower(),
            PaymentStatus.PENDING.value,
        )
