"""
Transaction Model

Represents payment transactions between customers and the system.

CRITICAL: All transaction status changes are logged.
Idempotency keys prevent duplicate processing.
"""

import enum
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Numeric,
    String,
    Text,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import audit_logger, get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.device import Device
    from app.models.device_activation import DeviceActivation

logger = get_logger(__name__)

# ISO 4217 currency codes (common ones for PAYGO)
VALID_CURRENCIES = {
    "USD", "EUR", "GBP", "XOF", "XAF", "NGN", "KES", "TZS", "UGX", "ZMW",
    "GHS", "RWF", "ETB", "ZAR", "INR", "BDT", "PKR", "PHP", "IDR", "VND",
}


class PaymentProvider(str, enum.Enum):
    """Supported payment providers."""

    WAVE = "wave"
    QMONEY = "qmoney"
    APPLE_PAY = "apple_pay"


class TransactionStatus(str, enum.Enum):
    """Transaction status states."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


# Valid status transitions
VALID_STATUS_TRANSITIONS = {
    TransactionStatus.PENDING: {
        TransactionStatus.COMPLETED,
        TransactionStatus.FAILED,
    },
    TransactionStatus.COMPLETED: {TransactionStatus.REFUNDED},
    TransactionStatus.FAILED: set(),  # Terminal state
    TransactionStatus.REFUNDED: set(),  # Terminal state
}


class Transaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Transaction model.

    Attributes:
        id: Unique identifier (UUID)
        customer_id: Customer making the payment
        device_id: Device being paid for (optional)
        payment_provider: Payment provider used
        provider_transaction_id: Provider's transaction ID
        amount: Transaction amount
        currency: Currency code (ISO 4217)
        status: Transaction status
        idempotency_key: Idempotency key for deduplication
        failure_reason: Reason for failure (if failed)
        metadata: Additional transaction data
        completed_at: When transaction completed
    """

    __tablename__ = "transaction"

    # Customer making the payment
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        index=True,
        nullable=False,
    )

    # Device being paid for (optional - some payments may not be device-specific)
    device_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        index=True,
        nullable=True,
    )

    # Payment provider info
    payment_provider: Mapped[PaymentProvider] = mapped_column(
        Enum(PaymentProvider, name="payment_provider_enum"),
        nullable=False,
    )

    provider_transaction_id: Mapped[str] = mapped_column(
        String(200),
        unique=True,
        index=True,
        nullable=False,
    )

    # Amount and currency
    amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    # Transaction status
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status_enum"),
        default=TransactionStatus.PENDING,
        index=True,
        nullable=False,
    )

    # Idempotency key for deduplication
    idempotency_key: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    # Failure reason (if failed)
    failure_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Additional metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    # Completion timestamp
    completed_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=True,
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(
        "Customer",
        back_populates="transactions",
        lazy="selectin",
    )

    device: Mapped[Optional["Device"]] = relationship(
        "Device",
        lazy="selectin",
    )

    activations: Mapped[list["DeviceActivation"]] = relationship(
        "DeviceActivation",
        back_populates="transaction",
        lazy="dynamic",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Amount must be positive
        CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        # Composite indexes for common queries
        Index("ix_transaction_customer_status", "customer_id", "status"),
        Index("ix_transaction_device_status", "device_id", "status"),
        Index("ix_transaction_provider_status", "payment_provider", "status"),
        Index("ix_transaction_created_at", "created_at"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("amount")
    def validate_amount(self, key: str, value: Any) -> Decimal:
        """
        Validate amount is positive.

        LOUD: Raises ValueError if invalid.
        """
        if isinstance(value, (int, float)):
            value = Decimal(str(value))

        if value is None:
            logger.error("Transaction amount is None")
            raise ValueError("Transaction amount cannot be None")

        if value <= 0:
            logger.error(
                "Transaction amount not positive",
                amount=float(value),
            )
            raise ValueError(f"Transaction amount must be positive, got: {value}")

        # Round to 2 decimal places
        value = value.quantize(Decimal("0.01"))

        return value

    @validates("currency")
    def validate_currency(self, key: str, value: str) -> str:
        """
        Validate currency is valid ISO 4217 code.

        LOUD: Raises ValueError if invalid.
        """
        if not value:
            logger.error("Transaction currency is empty")
            raise ValueError("Transaction currency cannot be empty")

        value = value.upper().strip()

        if len(value) != 3:
            logger.error(
                "Transaction currency wrong length",
                currency=value,
                length=len(value),
            )
            raise ValueError(f"Currency code must be 3 characters, got: {value}")

        if value not in VALID_CURRENCIES:
            logger.warning(
                "Unknown currency code (allowing)",
                currency=value,
            )
            # Allow unknown currencies but warn

        return value

    @validates("provider_transaction_id")
    def validate_provider_transaction_id(self, key: str, value: str) -> str:
        """Validate provider transaction ID."""
        if not value:
            logger.error("Transaction provider_transaction_id is empty")
            raise ValueError("Transaction provider_transaction_id cannot be empty")

        value = value.strip()

        if len(value) > 200:
            logger.error(
                "Transaction provider_transaction_id too long",
                length=len(value),
            )
            raise ValueError(
                f"provider_transaction_id exceeds 200 characters: {len(value)}"
            )

        return value

    @validates("idempotency_key")
    def validate_idempotency_key(self, key: str, value: str) -> str:
        """Validate idempotency key."""
        if not value:
            logger.error("Transaction idempotency_key is empty")
            raise ValueError("Transaction idempotency_key cannot be empty")

        value = value.strip()

        if len(value) > 100:
            logger.error(
                "Transaction idempotency_key too long",
                length=len(value),
            )
            raise ValueError(f"idempotency_key exceeds 100 characters: {len(value)}")

        return value

    @validates("status")
    def validate_status(self, key: str, value: Any) -> TransactionStatus:
        """Validate status is a valid enum value."""
        if isinstance(value, str):
            try:
                return TransactionStatus(value)
            except ValueError:
                logger.error(
                    "Invalid transaction status",
                    value=value,
                    valid_values=[s.value for s in TransactionStatus],
                )
                raise ValueError(
                    f"Invalid transaction status: {value}. "
                    f"Valid values: {[s.value for s in TransactionStatus]}"
                )
        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def can_transition_to(self, new_status: TransactionStatus) -> bool:
        """Check if status transition is valid."""
        if self.status == new_status:
            return True
        valid_next = VALID_STATUS_TRANSITIONS.get(self.status, set())
        return new_status in valid_next

    def transition_status(
        self,
        new_status: TransactionStatus,
        reason: str = "",
        failure_reason: Optional[str] = None,
    ) -> None:
        """
        Transition transaction to new status.

        LOUD: Raises ValueError if transition is invalid.
        """
        if not self.can_transition_to(new_status):
            logger.error(
                "Invalid transaction status transition",
                transaction_id=str(self.id),
                current_status=self.status.value,
                new_status=new_status.value,
            )
            raise ValueError(
                f"Cannot transition from {self.status.value} to {new_status.value}"
            )

        old_status = self.status
        self.status = new_status

        if new_status == TransactionStatus.FAILED and failure_reason:
            self.failure_reason = failure_reason

        if new_status == TransactionStatus.COMPLETED:
            from datetime import datetime, timezone
            self.completed_at = datetime.now(timezone.utc)

        logger.info(
            "Transaction status changed",
            transaction_id=str(self.id),
            old_status=old_status.value,
            new_status=new_status.value,
            reason=reason,
        )

        # Audit log for financial transactions
        audit_logger.log_event(
            event_type="transaction",
            action="status_change",
            resource_type="transaction",
            resource_id=str(self.id),
            details={
                "old_status": old_status.value,
                "new_status": new_status.value,
                "reason": reason,
            },
        )

    def is_completed(self) -> bool:
        """Check if transaction is completed."""
        return self.status == TransactionStatus.COMPLETED

    def is_failed(self) -> bool:
        """Check if transaction failed."""
        return self.status == TransactionStatus.FAILED

    def is_pending(self) -> bool:
        """Check if transaction is pending."""
        return self.status == TransactionStatus.PENDING


# Event listener for logging transaction creation
@event.listens_for(Transaction, "after_insert")
def transaction_after_insert(mapper, connection, target):
    """Log transaction creation."""
    logger.info(
        "Transaction created",
        transaction_id=str(target.id),
        customer_id=str(target.customer_id),
        device_id=str(target.device_id) if target.device_id else None,
        amount=float(target.amount),
        currency=target.currency,
        payment_provider=target.payment_provider.value,
    )

    # Audit log
    audit_logger.log_event(
        event_type="transaction",
        action="create",
        resource_type="transaction",
        resource_id=str(target.id),
        details={
            "amount": float(target.amount),
            "currency": target.currency,
            "provider": target.payment_provider.value,
        },
    )
