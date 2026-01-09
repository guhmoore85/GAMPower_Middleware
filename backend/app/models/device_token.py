"""
DeviceToken Model

Stores generated tokens for devices with encryption and audit trail.

CRITICAL: Token values are encrypted at rest.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, event
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import audit_logger, get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.transaction import Transaction

logger = get_logger(__name__)


class DeviceToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    DeviceToken model for storing generated OpenPAYGO tokens.

    Attributes:
        id: Unique identifier (UUID)
        device_id: Device this token is for
        transaction_id: Transaction that triggered this token (optional)
        token_value: Encrypted token value
        token_type: Type of token (ADD_TIME, SET_TIME, etc.)
        days_added: Number of days this token adds
        counter_value: OpenPAYGO counter value used
        generated_at: When the token was generated
        expires_at: When the token expires
        is_used: Whether the token has been used
        used_at: When the token was used
        is_revoked: Whether the token has been revoked
        revoked_at: When the token was revoked
        revoked_reason: Reason for revocation
    """

    __tablename__ = "device_token"

    # Device relationship
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Transaction relationship (optional - manual tokens may not have one)
    transaction_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("transaction.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Token value (ENCRYPTED)
    token_value: Mapped[str] = mapped_column(
        String(500),  # Encrypted value is larger
        nullable=False,
    )

    # Token type (1=ADD_TIME, 2=SET_TIME, 3=DISABLE_PAYG, 4=COUNTER_SYNC)
    token_type: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
    )

    # Days this token adds/sets
    days_added: Mapped[int] = mapped_column(
        nullable=False,
    )

    # OpenPAYGO counter value
    counter_value: Mapped[int] = mapped_column(
        nullable=False,
    )

    # Timestamps
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Usage tracking
    is_used: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Revocation tracking
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    device: Mapped["Device"] = relationship(
        "Device",
        lazy="selectin",
    )

    transaction: Mapped[Optional["Transaction"]] = relationship(
        "Transaction",
        lazy="selectin",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        Index("ix_device_token_device_expires", "device_id", "expires_at"),
        Index("ix_device_token_device_used", "device_id", "is_used"),
        Index("ix_device_token_counter", "device_id", "counter_value"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("token_value")
    def validate_token_value(self, key: str, value: str) -> str:
        """Validate token value is provided."""
        if not value:
            logger.error("DeviceToken token_value is empty")
            raise ValueError("DeviceToken token_value cannot be empty")
        return value

    @validates("days_added")
    def validate_days_added(self, key: str, value: int) -> int:
        """Validate days_added is positive."""
        if value is None:
            logger.error("DeviceToken days_added is None")
            raise ValueError("DeviceToken days_added cannot be None")
        if value < 0:
            logger.error("DeviceToken days_added is negative", days_added=value)
            raise ValueError(f"DeviceToken days_added must be non-negative, got {value}")
        return value

    @validates("counter_value")
    def validate_counter_value(self, key: str, value: int) -> int:
        """Validate counter_value is non-negative."""
        if value is None:
            logger.error("DeviceToken counter_value is None")
            raise ValueError("DeviceToken counter_value cannot be None")
        if value < 0:
            logger.error("DeviceToken counter_value is negative", counter_value=value)
            raise ValueError(f"DeviceToken counter_value must be non-negative, got {value}")
        return value

    @validates("token_type")
    def validate_token_type(self, key: str, value: int) -> int:
        """Validate token_type is valid."""
        valid_types = {1, 2, 3, 4}  # ADD_TIME, SET_TIME, DISABLE_PAYG, COUNTER_SYNC
        if value not in valid_types:
            logger.error("DeviceToken token_type invalid", token_type=value)
            raise ValueError(f"DeviceToken token_type must be one of {valid_types}, got {value}")
        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def mark_as_used(self) -> None:
        """Mark token as used."""
        if self.is_used:
            logger.warning(
                "Token already marked as used",
                token_id=str(self.id),
                device_id=str(self.device_id),
            )
            return

        if self.is_revoked:
            logger.error(
                "Cannot use revoked token",
                token_id=str(self.id),
                device_id=str(self.device_id),
            )
            raise ValueError("Cannot use a revoked token")

        self.is_used = True
        self.used_at = datetime.now(timezone.utc)

        logger.info(
            "Token marked as used",
            token_id=str(self.id),
            device_id=str(self.device_id),
        )

    def revoke(self, reason: str) -> None:
        """Revoke the token."""
        if self.is_revoked:
            logger.warning(
                "Token already revoked",
                token_id=str(self.id),
                device_id=str(self.device_id),
            )
            return

        self.is_revoked = True
        self.revoked_at = datetime.now(timezone.utc)
        self.revoked_reason = reason

        logger.info(
            "Token revoked",
            token_id=str(self.id),
            device_id=str(self.device_id),
            reason=reason,
        )

    def is_valid(self) -> bool:
        """Check if token is still valid (not used, not revoked, not expired)."""
        if self.is_used:
            return False
        if self.is_revoked:
            return False
        if datetime.now(timezone.utc) > self.expires_at:
            return False
        return True

    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def token_type_name(self) -> str:
        """Get human-readable token type name."""
        names = {
            1: "ADD_TIME",
            2: "SET_TIME",
            3: "DISABLE_PAYG",
            4: "COUNTER_SYNC",
        }
        return names.get(self.token_type, "UNKNOWN")


# Event listener for logging token creation
@event.listens_for(DeviceToken, "after_insert")
def device_token_after_insert(mapper, connection, target):
    """Log token creation."""
    logger.info(
        "DeviceToken created",
        token_id=str(target.id),
        device_id=str(target.device_id),
        token_type=target.token_type_name,
        days_added=target.days_added,
        expires_at=target.expires_at.isoformat(),
    )

    audit_logger.log_event(
        event_type="token",
        action="create",
        resource_type="device_token",
        resource_id=str(target.id),
        details={
            "device_id": str(target.device_id),
            "token_type": target.token_type_name,
            "days_added": target.days_added,
            "transaction_id": str(target.transaction_id) if target.transaction_id else None,
        },
    )
