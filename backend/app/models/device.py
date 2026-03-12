"""
Device Model

Represents IoT devices (solar, e-mobility) managed by the PAYGO system.

CRITICAL: Device secret keys are encrypted at rest.
All validation errors are raised LOUDLY.
"""

import enum
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Text, event
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.device_activation import DeviceActivation
    from app.models.device_metric import DeviceMetric
    from app.models.payment_trigger import PaymentTrigger

logger = get_logger(__name__)


class DeviceType(str, enum.Enum):
    """Supported device types."""

    SOLAR = "solar"
    EMOBILITY = "emobility"


class DeviceStatus(str, enum.Enum):
    """Device status states."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


# Valid status transitions
VALID_STATUS_TRANSITIONS = {
    DeviceStatus.ACTIVE: {DeviceStatus.SUSPENDED, DeviceStatus.INACTIVE},
    DeviceStatus.SUSPENDED: {DeviceStatus.ACTIVE, DeviceStatus.INACTIVE},
    DeviceStatus.INACTIVE: {DeviceStatus.ACTIVE},  # Can reactivate
}


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Device model representing IoT devices.

    Attributes:
        id: Unique identifier (UUID)
        external_id: External identifier from manufacturer/partner
        device_type: Type of device (solar, emobility)
        manufacturer: Device manufacturer name
        model: Device model name
        openpaygo_secret_key: Encrypted OpenPAYGO secret key
        status: Current device status
        metadata: Additional device metadata (JSON)
        customer_id: Associated customer (optional)
    """

    __tablename__ = "device"

    # External identifier - unique across all devices
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    # Device type
    device_type: Mapped[DeviceType] = mapped_column(
        Enum(DeviceType, name="device_type_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    # Manufacturer and model info
    manufacturer: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # OpenPAYGO secret key (ENCRYPTED)
    openpaygo_secret_key: Mapped[str] = mapped_column(
        String(500),  # Encrypted value is larger than original
        nullable=False,
    )

    # Device status
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, name="device_status_enum", values_callable=lambda x: [e.value for e in x]),
        default=DeviceStatus.ACTIVE,
        index=True,
        nullable=False,
    )

    # Additional metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    # Customer relationship
    customer_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customer.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Relationships
    customer: Mapped[Optional["Customer"]] = relationship(
        "Customer",
        back_populates="devices",
        lazy="selectin",
    )

    metrics: Mapped[list["DeviceMetric"]] = relationship(
        "DeviceMetric",
        back_populates="device",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    activations: Mapped[list["DeviceActivation"]] = relationship(
        "DeviceActivation",
        back_populates="device",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    triggers: Mapped[list["PaymentTrigger"]] = relationship(
        "PaymentTrigger",
        back_populates="device",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Index for common queries
        Index("ix_device_status_type", "status", "device_type"),
        Index("ix_device_customer_status", "customer_id", "status"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("external_id")
    def validate_external_id(self, key: str, value: str) -> str:
        """
        Validate external_id format.

        LOUD: Raises ValueError with details if invalid.
        """
        if not value:
            logger.error("Device external_id is empty")
            raise ValueError("Device external_id cannot be empty")

        if len(value) > 100:
            logger.error(
                "Device external_id too long",
                length=len(value),
                max_length=100,
            )
            raise ValueError(f"Device external_id exceeds 100 characters: {len(value)}")

        # Allow alphanumeric, hyphens, underscores
        if not re.match(r"^[\w\-]+$", value):
            logger.error(
                "Device external_id invalid format",
                external_id=value,
            )
            raise ValueError(
                f"Device external_id contains invalid characters: {value}. "
                "Only alphanumeric, hyphens, and underscores allowed."
            )

        return value

    @validates("device_type")
    def validate_device_type(self, key: str, value: Any) -> DeviceType:
        """Validate device_type is a valid enum value."""
        if isinstance(value, str):
            try:
                return DeviceType(value)
            except ValueError:
                logger.error(
                    "Invalid device_type",
                    value=value,
                    valid_values=[t.value for t in DeviceType],
                )
                raise ValueError(
                    f"Invalid device_type: {value}. "
                    f"Valid values: {[t.value for t in DeviceType]}"
                )
        return value

    @validates("status")
    def validate_status(self, key: str, value: Any) -> DeviceStatus:
        """Validate status is a valid enum value."""
        if isinstance(value, str):
            try:
                return DeviceStatus(value)
            except ValueError:
                logger.error(
                    "Invalid device status",
                    value=value,
                    valid_values=[s.value for s in DeviceStatus],
                )
                raise ValueError(
                    f"Invalid device status: {value}. "
                    f"Valid values: {[s.value for s in DeviceStatus]}"
                )
        return value

    @validates("openpaygo_secret_key")
    def validate_secret_key(self, key: str, value: str) -> str:
        """
        Validate secret key is provided.

        Note: Value should already be encrypted when stored.
        """
        if not value:
            logger.error("Device openpaygo_secret_key is empty")
            raise ValueError("Device openpaygo_secret_key cannot be empty")

        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def can_transition_to(self, new_status: DeviceStatus) -> bool:
        """Check if status transition is valid."""
        if self.status == new_status:
            return True
        valid_next = VALID_STATUS_TRANSITIONS.get(self.status, set())
        return new_status in valid_next

    def transition_status(self, new_status: DeviceStatus, reason: str = "") -> None:
        """
        Transition device to new status.

        LOUD: Raises ValueError if transition is invalid.
        """
        if not self.can_transition_to(new_status):
            logger.error(
                "Invalid status transition",
                device_id=str(self.id),
                current_status=self.status.value,
                new_status=new_status.value,
            )
            raise ValueError(
                f"Cannot transition from {self.status.value} to {new_status.value}"
            )

        old_status = self.status
        self.status = new_status

        logger.info(
            "Device status changed",
            device_id=str(self.id),
            old_status=old_status.value,
            new_status=new_status.value,
            reason=reason,
        )

    def is_active(self) -> bool:
        """Check if device is active."""
        return self.status == DeviceStatus.ACTIVE

    def is_suspended(self) -> bool:
        """Check if device is suspended."""
        return self.status == DeviceStatus.SUSPENDED


# Event listener for logging device creation
@event.listens_for(Device, "after_insert")
def device_after_insert(mapper, connection, target):
    """Log device creation."""
    logger.info(
        "Device created",
        device_id=str(target.id),
        external_id=target.external_id,
        device_type=target.device_type.value,
    )


# Event listener for logging status changes
@event.listens_for(Device.status, "set")
def device_status_set(target, value, oldvalue, initiator):
    """Log device status changes."""
    if oldvalue is not None and oldvalue != value:
        logger.info(
            "Device status changing",
            device_id=str(target.id) if target.id else "new",
            old_status=oldvalue.value if isinstance(oldvalue, DeviceStatus) else oldvalue,
            new_status=value.value if isinstance(value, DeviceStatus) else value,
        )
