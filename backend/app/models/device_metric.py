"""
DeviceMetric Model

Represents telemetry data from IoT devices.

CRITICAL: All metrics are validated and logged.
Future consideration: Partition by timestamp for large datasets.
"""

import enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import get_logger
from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.device import Device

logger = get_logger(__name__)


class MetricType(str, enum.Enum):
    """Supported metric types."""

    ENERGY_CONSUMED = "energy_consumed"  # kWh
    DISTANCE_TRAVELED = "distance_traveled"  # km
    BATTERY_LEVEL = "battery_level"  # percentage (0-100)
    UPTIME = "uptime"  # hours


# Valid units for each metric type
METRIC_UNITS = {
    MetricType.ENERGY_CONSUMED: {"kWh", "Wh", "MWh"},
    MetricType.DISTANCE_TRAVELED: {"km", "mi", "m"},
    MetricType.BATTERY_LEVEL: {"%", "percent"},
    MetricType.UPTIME: {"hours", "minutes", "seconds", "h", "m", "s"},
}

# Default units for each metric type
DEFAULT_UNITS = {
    MetricType.ENERGY_CONSUMED: "kWh",
    MetricType.DISTANCE_TRAVELED: "km",
    MetricType.BATTERY_LEVEL: "%",
    MetricType.UPTIME: "hours",
}


class DeviceMetric(Base, UUIDPrimaryKeyMixin):
    """
    DeviceMetric model.

    Attributes:
        id: Unique identifier (UUID)
        device_id: Device this metric belongs to
        metric_type: Type of metric
        value: Metric value
        unit: Unit of measurement
        timestamp: When the metric was recorded
        metadata: Additional metric data
        created_at: When the record was created
    """

    __tablename__ = "device_metric"

    # Device relationship
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("device.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Metric info
    metric_type: Mapped[MetricType] = mapped_column(
        Enum(MetricType, name="metric_type_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    value: Mapped[Decimal] = mapped_column(
        Numeric(15, 4),
        nullable=False,
    )

    unit: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Timestamp of the metric reading
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
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

    # Record creation timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    device: Mapped["Device"] = relationship(
        "Device",
        back_populates="metrics",
        lazy="selectin",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Value must be non-negative
        CheckConstraint("value >= 0", name="ck_device_metric_value_non_negative"),
        # Composite index for efficient time-series queries
        Index("ix_device_metric_device_timestamp", "device_id", "timestamp"),
        Index("ix_device_metric_device_type_timestamp", "device_id", "metric_type", "timestamp"),
        # Note: For production with high volume metrics, consider:
        # - TimescaleDB extension for automatic partitioning
        # - Partition by month using PostgreSQL native partitioning
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("value")
    def validate_value(self, key: str, value: Any) -> Decimal:
        """
        Validate metric value is non-negative.

        LOUD: Raises ValueError if invalid.
        """
        if isinstance(value, (int, float)):
            value = Decimal(str(value))

        if value is None:
            logger.error("DeviceMetric value is None")
            raise ValueError("DeviceMetric value cannot be None")

        if value < 0:
            logger.error(
                "DeviceMetric value is negative",
                value=float(value),
            )
            raise ValueError(f"DeviceMetric value must be non-negative, got: {value}")

        return value

    @validates("metric_type")
    def validate_metric_type(self, key: str, value: Any) -> MetricType:
        """Validate metric_type is a valid enum value."""
        if isinstance(value, str):
            try:
                return MetricType(value)
            except ValueError:
                logger.error(
                    "Invalid metric_type",
                    value=value,
                    valid_values=[t.value for t in MetricType],
                )
                raise ValueError(
                    f"Invalid metric_type: {value}. "
                    f"Valid values: {[t.value for t in MetricType]}"
                )
        return value

    @validates("unit")
    def validate_unit(self, key: str, value: str) -> str:
        """
        Validate unit is appropriate for metric type.

        LOUD: Raises ValueError if invalid.
        """
        if not value:
            logger.error("DeviceMetric unit is empty")
            raise ValueError("DeviceMetric unit cannot be empty")

        value = value.strip()

        if len(value) > 20:
            logger.error(
                "DeviceMetric unit too long",
                length=len(value),
                max_length=20,
            )
            raise ValueError(f"DeviceMetric unit exceeds 20 characters: {len(value)}")

        return value

    @validates("timestamp")
    def validate_timestamp(self, key: str, value: datetime) -> datetime:
        """
        Validate timestamp is not in the future.

        LOUD: Raises ValueError if invalid.
        """
        if value is None:
            logger.error("DeviceMetric timestamp is None")
            raise ValueError("DeviceMetric timestamp cannot be None")

        # Ensure timezone aware
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # Allow slight clock drift (5 minutes)
        if value > now + timedelta(minutes=5):
            logger.error(
                "DeviceMetric timestamp is in the future",
                timestamp=value.isoformat(),
                now=now.isoformat(),
            )
            raise ValueError(
                f"DeviceMetric timestamp cannot be in the future: {value.isoformat()}"
            )

        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def is_valid_unit_for_type(self) -> bool:
        """Check if unit is valid for metric type."""
        valid_units = METRIC_UNITS.get(self.metric_type, set())
        return self.unit in valid_units


# Need to import timedelta for validator
from datetime import timedelta  # noqa: E402


# Event listener for logging metric creation
@event.listens_for(DeviceMetric, "after_insert")
def device_metric_after_insert(mapper, connection, target):
    """Log metric creation."""
    logger.debug(
        "Device metric recorded",
        metric_id=str(target.id),
        device_id=str(target.device_id),
        metric_type=target.metric_type.value,
        value=float(target.value),
        unit=target.unit,
        timestamp=target.timestamp.isoformat(),
    )
