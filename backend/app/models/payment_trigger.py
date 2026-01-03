"""
PaymentTrigger Model

Represents triggers that initiate payment requests based on usage or time.

CRITICAL: Trigger configuration is validated against schema.
"""

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Index,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.device import Device

logger = get_logger(__name__)


class TriggerType(str, enum.Enum):
    """Types of payment triggers."""

    USAGE_THRESHOLD = "usage_threshold"  # Trigger when usage exceeds threshold
    TIME_BASED = "time_based"  # Trigger at specific intervals
    MANUAL = "manual"  # Manually triggered payments


# Configuration schemas for each trigger type
TRIGGER_CONFIG_SCHEMAS = {
    TriggerType.USAGE_THRESHOLD: {
        "required": ["metric_type", "threshold"],
        "optional": ["amount", "currency"],
        "types": {
            "metric_type": str,
            "threshold": (int, float),
            "amount": (int, float),
            "currency": str,
        },
    },
    TriggerType.TIME_BASED: {
        "required": ["interval_days"],
        "optional": ["amount", "currency", "start_date"],
        "types": {
            "interval_days": int,
            "amount": (int, float),
            "currency": str,
            "start_date": str,
        },
    },
    TriggerType.MANUAL: {
        "required": [],
        "optional": ["amount", "currency", "notes"],
        "types": {
            "amount": (int, float),
            "currency": str,
            "notes": str,
        },
    },
}


class PaymentTrigger(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    PaymentTrigger model.

    Attributes:
        id: Unique identifier (UUID)
        device_id: Device this trigger belongs to
        trigger_type: Type of trigger
        config: Trigger configuration (JSON)
        is_active: Whether trigger is active
        last_triggered_at: When trigger was last fired
    """

    __tablename__ = "payment_trigger"

    # Device this trigger belongs to
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        index=True,
        nullable=False,
    )

    # Trigger type
    trigger_type: Mapped[TriggerType] = mapped_column(
        Enum(TriggerType, name="trigger_type_enum"),
        nullable=False,
    )

    # Trigger configuration
    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # Active status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        index=True,
        nullable=False,
    )

    # Last triggered timestamp
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    device: Mapped["Device"] = relationship(
        "Device",
        back_populates="triggers",
        lazy="selectin",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Only one active trigger of each type per device
        UniqueConstraint(
            "device_id",
            "trigger_type",
            name="uq_payment_trigger_device_type",
        ),
        # Composite indexes
        Index("ix_payment_trigger_active", "is_active"),
        Index("ix_payment_trigger_device_active", "device_id", "is_active"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("trigger_type")
    def validate_trigger_type(self, key: str, value: Any) -> TriggerType:
        """Validate trigger_type is a valid enum value."""
        if isinstance(value, str):
            try:
                return TriggerType(value)
            except ValueError:
                logger.error(
                    "Invalid trigger_type",
                    value=value,
                    valid_values=[t.value for t in TriggerType],
                )
                raise ValueError(
                    f"Invalid trigger_type: {value}. "
                    f"Valid values: {[t.value for t in TriggerType]}"
                )
        return value

    @validates("config")
    def validate_config(self, key: str, value: dict) -> dict:
        """
        Validate config matches trigger type schema.

        LOUD: Raises ValueError with details if invalid.
        """
        if not isinstance(value, dict):
            logger.error(
                "PaymentTrigger config is not a dict",
                type=type(value).__name__,
            )
            raise ValueError("PaymentTrigger config must be a dictionary")

        # Note: Full validation happens in validate_config_for_type()
        # because we might not have trigger_type set yet during object creation
        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def validate_config_for_type(self) -> list[str]:
        """
        Validate config against trigger type schema.

        Returns list of validation errors (empty if valid).

        LOUD: Logs all validation errors.
        """
        errors = []
        schema = TRIGGER_CONFIG_SCHEMAS.get(self.trigger_type)

        if not schema:
            errors.append(f"No schema defined for trigger type: {self.trigger_type}")
            logger.error(
                "No schema for trigger type",
                trigger_type=self.trigger_type.value,
            )
            return errors

        # Check required fields
        for field in schema["required"]:
            if field not in self.config:
                errors.append(f"Missing required field: {field}")
                logger.error(
                    "Missing required config field",
                    trigger_id=str(self.id) if self.id else "new",
                    trigger_type=self.trigger_type.value,
                    field=field,
                )

        # Check field types
        for field, expected_types in schema["types"].items():
            if field in self.config:
                value = self.config[field]
                if not isinstance(value, expected_types):
                    errors.append(
                        f"Field '{field}' has wrong type: expected {expected_types}, "
                        f"got {type(value).__name__}"
                    )
                    logger.error(
                        "Wrong config field type",
                        trigger_id=str(self.id) if self.id else "new",
                        field=field,
                        expected=str(expected_types),
                        got=type(value).__name__,
                    )

        # Type-specific validations
        if self.trigger_type == TriggerType.USAGE_THRESHOLD:
            if "threshold" in self.config and self.config["threshold"] <= 0:
                errors.append("threshold must be positive")
                logger.error(
                    "Invalid threshold value",
                    threshold=self.config["threshold"],
                )

        elif self.trigger_type == TriggerType.TIME_BASED:
            if "interval_days" in self.config:
                interval = self.config["interval_days"]
                if interval <= 0 or interval > 365:
                    errors.append("interval_days must be between 1 and 365")
                    logger.error(
                        "Invalid interval_days value",
                        interval_days=interval,
                    )

        # Validate amount if present
        if "amount" in self.config and self.config["amount"] <= 0:
            errors.append("amount must be positive")
            logger.error(
                "Invalid amount value",
                amount=self.config["amount"],
            )

        return errors

    def mark_triggered(self) -> None:
        """Mark the trigger as triggered now."""
        self.last_triggered_at = datetime.now(timezone.utc)
        logger.info(
            "Trigger fired",
            trigger_id=str(self.id),
            device_id=str(self.device_id),
            trigger_type=self.trigger_type.value,
        )

    def deactivate(self, reason: str = "") -> None:
        """Deactivate the trigger."""
        self.is_active = False
        logger.info(
            "Trigger deactivated",
            trigger_id=str(self.id),
            device_id=str(self.device_id),
            reason=reason,
        )

    def activate(self) -> None:
        """Activate the trigger."""
        self.is_active = True
        logger.info(
            "Trigger activated",
            trigger_id=str(self.id),
            device_id=str(self.device_id),
        )

    def get_threshold(self) -> Optional[float]:
        """Get usage threshold (for usage triggers)."""
        return self.config.get("threshold")

    def get_interval_days(self) -> Optional[int]:
        """Get interval days (for time-based triggers)."""
        return self.config.get("interval_days")

    def get_amount(self) -> Optional[float]:
        """Get payment amount from config."""
        return self.config.get("amount")

    def get_currency(self) -> Optional[str]:
        """Get currency from config."""
        return self.config.get("currency")


# Event listener for logging trigger creation
@event.listens_for(PaymentTrigger, "after_insert")
def payment_trigger_after_insert(mapper, connection, target):
    """Log trigger creation."""
    logger.info(
        "Payment trigger created",
        trigger_id=str(target.id),
        device_id=str(target.device_id),
        trigger_type=target.trigger_type.value,
        is_active=target.is_active,
        config_keys=list(target.config.keys()),
    )


# Event listener for logging trigger activation changes
@event.listens_for(PaymentTrigger.is_active, "set")
def payment_trigger_is_active_set(target, value, oldvalue, initiator):
    """Log trigger activation status changes."""
    if oldvalue is not None and oldvalue != value:
        logger.info(
            "Payment trigger status changed",
            trigger_id=str(target.id) if target.id else "new",
            old_active=oldvalue,
            new_active=value,
        )
