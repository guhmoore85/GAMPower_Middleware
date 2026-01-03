"""
Base Payment Provider

Abstract base class for all payment providers.

CRITICAL: All payment operations must:
1. Log request details (sanitized)
2. Log response details
3. Handle errors loudly
4. Never log credentials or tokens
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from app.core.exceptions import PaymentProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)


class PaymentStatus(str, Enum):
    """Payment status states."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentResult:
    """
    Result of a payment operation.

    Contains all relevant details for logging and processing.
    """

    def __init__(
        self,
        success: bool,
        transaction_id: Optional[str] = None,
        status: PaymentStatus = PaymentStatus.PENDING,
        *,
        payment_url: Optional[str] = None,
        provider_response: Optional[dict] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.success = success
        self.transaction_id = transaction_id
        self.status = status
        self.payment_url = payment_url
        self.provider_response = provider_response or {}
        self.error_code = error_code
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "transaction_id": self.transaction_id,
            "status": self.status.value if self.status else None,
            "payment_url": self.payment_url,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class RefundResult:
    """Result of a refund operation."""

    def __init__(
        self,
        success: bool,
        refund_id: Optional[str] = None,
        *,
        amount: Optional[Decimal] = None,
        provider_response: Optional[dict] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.success = success
        self.refund_id = refund_id
        self.amount = amount
        self.provider_response = provider_response or {}
        self.error_code = error_code
        self.error_message = error_message


class PaymentProvider(ABC):
    """
    Abstract base class for payment providers.

    All concrete implementations must:
    1. Implement all abstract methods
    2. Log all operations loudly
    3. Handle errors and return appropriate results
    4. Never expose credentials in logs or responses

    MOCK IMPLEMENTATIONS: Clearly marked with TODO comments
    for real API integration.
    """

    # Provider name for logging
    PROVIDER_NAME: str = "unknown"

    def __init__(self, api_key: str, api_url: str, webhook_secret: str):
        """
        Initialize payment provider.

        Args:
            api_key: API key for authentication
            api_url: Base URL for API calls
            webhook_secret: Secret for webhook signature verification
        """
        self.api_key = api_key
        self.api_url = api_url
        self.webhook_secret = webhook_secret

        logger.info(
            f"{self.PROVIDER_NAME} payment provider initialized",
            api_url=api_url,
            # NEVER log api_key or webhook_secret
        )

    @abstractmethod
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
        Initialize a payment request.

        Args:
            amount: Payment amount
            currency: Currency code (ISO 4217)
            customer_id: Customer UUID
            metadata: Additional metadata to include
            callback_url: URL to redirect after payment

        Returns:
            PaymentResult with transaction_id and payment_url

        Raises:
            PaymentProviderError: If payment initialization fails

        LOUD: Logs request and response (sanitized)
        """
        pass

    @abstractmethod
    async def verify_webhook(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Verify webhook signature.

        Args:
            payload: Raw webhook payload bytes
            signature: Signature from webhook headers

        Returns:
            True if signature is valid

        LOUD: Logs verification attempt and result
        """
        pass

    @abstractmethod
    async def get_transaction_status(
        self,
        transaction_id: str,
    ) -> PaymentStatus:
        """
        Get transaction status from provider.

        Args:
            transaction_id: Provider's transaction ID

        Returns:
            Current PaymentStatus

        Raises:
            PaymentProviderError: If status check fails

        LOUD: Logs status check and result
        """
        pass

    @abstractmethod
    async def refund_transaction(
        self,
        transaction_id: str,
        *,
        amount: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> RefundResult:
        """
        Refund a transaction.

        Args:
            transaction_id: Provider's transaction ID
            amount: Partial refund amount (None for full refund)
            reason: Reason for refund

        Returns:
            RefundResult with refund details

        Raises:
            PaymentProviderError: If refund fails

        LOUD: Logs refund request and result
        """
        pass

    @abstractmethod
    def parse_webhook_payload(
        self,
        payload: dict,
    ) -> dict:
        """
        Parse webhook payload into standardized format.

        Args:
            payload: Raw webhook payload

        Returns:
            Standardized payload with:
            - transaction_id
            - status
            - amount
            - currency
            - metadata

        LOUD: Logs parsed fields
        """
        pass

    def _log_request(
        self,
        operation: str,
        **kwargs: Any,
    ) -> None:
        """
        Log outgoing request details.

        CRITICAL: Sanitizes sensitive data.
        """
        # Remove sensitive fields
        safe_kwargs = {
            k: v for k, v in kwargs.items()
            if not any(s in k.lower() for s in ["key", "secret", "token", "password"])
        }

        logger.info(
            f"{self.PROVIDER_NAME} API request: {operation}",
            provider=self.PROVIDER_NAME,
            operation=operation,
            **safe_kwargs,
        )

    def _log_response(
        self,
        operation: str,
        success: bool,
        **kwargs: Any,
    ) -> None:
        """Log response details."""
        safe_kwargs = {
            k: v for k, v in kwargs.items()
            if not any(s in k.lower() for s in ["key", "secret", "token", "password"])
        }

        if success:
            logger.info(
                f"{self.PROVIDER_NAME} API response: {operation} succeeded",
                provider=self.PROVIDER_NAME,
                operation=operation,
                **safe_kwargs,
            )
        else:
            logger.warning(
                f"{self.PROVIDER_NAME} API response: {operation} failed",
                provider=self.PROVIDER_NAME,
                operation=operation,
                **safe_kwargs,
            )

    def _log_error(
        self,
        operation: str,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Log error with context."""
        logger.error(
            f"{self.PROVIDER_NAME} API error: {operation}",
            provider=self.PROVIDER_NAME,
            operation=operation,
            error=str(error),
            error_type=type(error).__name__,
            **kwargs,
        )
