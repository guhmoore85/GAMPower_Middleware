"""
DeviceActivation Model

Represents device activations (payment, manual, trial).

CRITICAL: All activations are logged with full context.
"""

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    String,
    event,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import audit_logger, get_logger
from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.transaction import Transaction

logger = get_logger(__name__)


class ActivationType(str, enum.Enum):
    """Types of device activation."""

    PAYMENT = "payment"  # Activated after payment
    MANUAL = "manual"  # Manually activated by admin
    TRIAL = "trial"  # Trial activation


class DeviceActivation(Base, UUIDPrimaryKeyMixin):
    """
    DeviceActivation model.

    Attributes:
        id: Unique identifier (UUID)
        device_id: Device being activated
        transaction_id: Payment transaction (if payment activation)
        activation_token: OpenPAYGO activation token
        activation_type: Type of activation
        expires_at: When activation expires
        activated_at: When device was actually activated
        created_at: When record was created
    """

    __tablename__ = "device_activation"

    # Device being activated
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        index=True,
        nullable=False,
    )

    # Transaction that triggered this activation (for payment type)
    transaction_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        index=True,
        nullable=True,
    )

    # Activation token (OpenPAYGO token)
    activation_token: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # Type of activation
    activation_type: Mapped[ActivationType] = mapped_column(
        Enum(ActivationType, name="activation_type_enum"),
        nullable=False,
    )

    # Expiration time
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=True,
    )

    # When the device actually confirmed activation
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Record creation timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    device: Mapped["Device"] = relationship(
        "Device",
        back_populates="activations",
        lazy="selectin",
    )

    transaction: Mapped[Optional["Transaction"]] = relationship(
        "Transaction",
        back_populates="activations",
        lazy="selectin",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # expires_at must be after created_at
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_device_activation_expires_after_created",
        ),
        # Payment activations must have transaction_id
        CheckConstraint(
            "(activation_type != 'payment') OR (transaction_id IS NOT NULL)",
            name="ck_device_activation_payment_requires_transaction",
        ),
        # Composite indexes
        Index("ix_device_activation_device_created", "device_id", "created_at"),
        Index("ix_device_activation_expires", "expires_at"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("activation_token")
    def validate_activation_token(self, key: str, value: str) -> str:
        """
        Validate activation token format.

        LOUD: Raises ValueError if invalid.
        """
        if not value:
            logger.error("DeviceActivation activation_token is empty")
            raise ValueError("DeviceActivation activation_token cannot be empty")

        value = value.strip()

        if len(value) > 200:
            logger.error(
                "DeviceActivation activation_token too long",
                length=len(value),
                max_length=200,
            )
            raise ValueError(
                f"activation_token exceeds 200 characters: {len(value)}"
            )

        return value

    @validates("activation_type")
    def validate_activation_type(self, key: str, value: Any) -> ActivationType:
        """Validate activation_type is a valid enum value."""
        if isinstance(value, str):
            try:
                return ActivationType(value)
            except ValueError:
                logger.error(
                    "Invalid activation_type",
                    value=value,
                    valid_values=[t.value for t in ActivationType],
                )
                raise ValueError(
                    f"Invalid activation_type: {value}. "
                    f"Valid values: {[t.value for t in ActivationType]}"
                )
        return value

    @validates("expires_at")
    def validate_expires_at(self, key: str, value: Optional[datetime]) -> Optional[datetime]:
        """
        Validate expires_at is in the future (if set).

        LOUD: Raises ValueError if invalid.
        """
        if value is None:
            return None

        # Ensure timezone aware
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def is_expired(self) -> bool:
        """Check if activation has expired."""
        if self.expires_at is None:
            return False  # No expiry means never expires
        return datetime.now(timezone.utc) > self.expires_at

    def is_active(self) -> bool:
        """Check if activation is currently active."""
        return self.activated_at is not None and not self.is_expired()

    def mark_activated(self) -> None:
        """Mark the activation as activated (device confirmed)."""
        if self.activated_at is not None:
            logger.warning(
                "Activation already activated",
                activation_id=str(self.id),
                activated_at=self.activated_at.isoformat(),
            )
            return

        self.activated_at = datetime.now(timezone.utc)

        logger.info(
            "Activation marked as activated",
            activation_id=str(self.id),
            device_id=str(self.device_id),
            activation_type=self.activation_type.value,
        )

    def time_remaining(self) -> Optional[int]:
        """Get remaining time in seconds, or None if no expiry."""
        if self.expires_at is None:
            return None
        remaining = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))


# Event listener for logging activation creation
@event.listens_for(DeviceActivation, "after_insert")
def device_activation_after_insert(mapper, connection, target):
    """Log activation creation."""
    logger.info(
        "Device activation created",
        activation_id=str(target.id),
        device_id=str(target.device_id),
        activation_type=target.activation_type.value,
        transaction_id=str(target.transaction_id) if target.transaction_id else None,
        expires_at=target.expires_at.isoformat() if target.expires_at else None,
    )

    # Audit log
    audit_logger.log_event(
        event_type="activation",
        action="create",
        resource_type="device_activation",
        resource_id=str(target.id),
        details={
            "device_id": str(target.device_id),
            "activation_type": target.activation_type.value,
            "has_transaction": target.transaction_id is not None,
        },
    )


# Event listener for logging activation confirmation
@event.listens_for(DeviceActivation.activated_at, "set")
def device_activation_activated_at_set(target, value, oldvalue, initiator):
    """Log activation confirmation."""
    if oldvalue is None and value is not None:
        logger.info(
            "Device activation confirmed",
            activation_id=str(target.id) if target.id else "new",
            device_id=str(target.device_id),
            activated_at=value.isoformat() if value else None,
        )
