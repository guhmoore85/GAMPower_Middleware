"""
Transaction Schemas

Request/response schemas for payment transactions.
CRITICAL: Amount validation is strict (must be positive).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.models.transaction import PaymentProvider, TransactionStatus
from app.schemas.common import BaseSchema, TimestampMixin


class TransactionBase(BaseSchema):
    """Base transaction schema."""

    customer_id: UUID = Field(..., description="Customer making the payment")
    device_id: Optional[UUID] = Field(
        None, description="Device being paid for (optional)"
    )
    amount: Decimal = Field(
        ...,
        gt=0,
        max_digits=10,
        decimal_places=2,
        description="Transaction amount (must be positive)",
    )
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Currency code (ISO 4217, e.g., USD, XOF)",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional transaction metadata",
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Normalize currency code to uppercase."""
        return v.upper().strip()

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v: Any) -> Decimal:
        """Ensure amount is a valid decimal."""
        if isinstance(v, (int, float)):
            v = Decimal(str(v))
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


class TransactionCreate(TransactionBase):
    """
    Schema for creating a transaction.

    Usually created by payment webhooks, not directly.
    """

    payment_provider: PaymentProvider = Field(
        ..., description="Payment provider"
    )
    provider_transaction_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Transaction ID from payment provider",
    )
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Idempotency key for deduplication",
    )


class TransactionUpdate(BaseSchema):
    """
    Schema for updating a transaction.

    Only status updates are typically allowed.
    """

    status: TransactionStatus = Field(
        ..., description="New transaction status"
    )
    failure_reason: Optional[str] = Field(
        None,
        max_length=1000,
        description="Reason for failure (if status is 'failed')",
    )


class TransactionResponse(TimestampMixin):
    """
    Transaction response schema.
    """

    model_config = {"from_attributes": True}

    id: UUID = Field(..., description="Transaction UUID")
    customer_id: UUID = Field(..., description="Customer ID")
    device_id: Optional[UUID] = Field(None, description="Device ID")
    payment_provider: PaymentProvider = Field(..., description="Payment provider")
    provider_transaction_id: str = Field(
        ..., description="Provider's transaction ID"
    )
    amount: Decimal = Field(..., description="Transaction amount")
    currency: str = Field(..., description="Currency code")
    status: TransactionStatus = Field(..., description="Transaction status")
    idempotency_key: str = Field(..., description="Idempotency key")
    failure_reason: Optional[str] = Field(None, description="Failure reason")
    metadata: Optional[dict[str, Any]] = Field(None, description="Metadata")
    completed_at: Optional[datetime] = Field(None, description="Completion time")


class TransactionWithDetails(TransactionResponse):
    """Transaction with related entities."""

    customer_external_id: Optional[str] = Field(
        None, description="Customer external ID"
    )
    device_external_id: Optional[str] = Field(
        None, description="Device external ID"
    )
    activations: list["TransactionActivationSummary"] = Field(
        default_factory=list,
        description="Related activations",
    )


class TransactionActivationSummary(BaseSchema):
    """Summary of transaction-related activation."""

    id: UUID = Field(..., description="Activation ID")
    activation_type: str = Field(..., description="Activation type")
    expires_at: Optional[datetime] = Field(None, description="Expiration time")
    activated_at: Optional[datetime] = Field(None, description="Activation time")


class WebhookPayload(BaseSchema):
    """
    Generic webhook payload schema.

    Each provider has specific validation.
    """

    event_type: str = Field(..., description="Event type")
    transaction_id: str = Field(..., description="Provider transaction ID")
    status: str = Field(..., description="Transaction status")
    amount: Optional[Decimal] = Field(None, description="Transaction amount")
    currency: Optional[str] = Field(None, description="Currency code")
    timestamp: Optional[datetime] = Field(None, description="Event timestamp")
    metadata: Optional[dict[str, Any]] = Field(None, description="Event metadata")


class WaveWebhookPayload(BaseSchema):
    """Wave-specific webhook payload."""

    event: str = Field(..., description="Event type")
    data: dict[str, Any] = Field(..., description="Event data")


class QMoneyWebhookPayload(BaseSchema):
    """QMoney-specific webhook payload."""

    notification_type: str = Field(..., description="Notification type")
    transaction_reference: str = Field(..., description="Transaction reference")
    amount: Decimal = Field(..., description="Transaction amount")
    currency: str = Field(..., description="Currency code")
    status: str = Field(..., description="Transaction status")
    timestamp: datetime = Field(..., description="Event timestamp")


class ApplePayWebhookPayload(BaseSchema):
    """Apple Pay-specific webhook payload."""

    notification_id: str = Field(..., description="Notification ID")
    notification_type: str = Field(..., description="Notification type")
    data: dict[str, Any] = Field(..., description="Notification data")


# Update forward references
TransactionWithDetails.model_rebuild()
