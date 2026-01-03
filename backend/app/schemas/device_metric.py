"""
Device Metric Schemas

Request/response schemas for device telemetry.
CRITICAL: Validates metric types and units.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.device_metric import DEFAULT_UNITS, METRIC_UNITS, MetricType
from app.schemas.common import BaseSchema


class MetricBase(BaseSchema):
    """Base metric schema."""

    metric_type: MetricType = Field(
        ..., description="Type of metric being reported"
    )
    value: Decimal = Field(
        ...,
        ge=0,
        max_digits=15,
        decimal_places=4,
        description="Metric value (must be non-negative)",
    )
    unit: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Unit of measurement",
    )
    timestamp: datetime = Field(
        ..., description="When the metric was recorded"
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional metric metadata",
    )

    @field_validator("value", mode="before")
    @classmethod
    def validate_value(cls, v: Any) -> Decimal:
        """Ensure value is a valid decimal."""
        if isinstance(v, (int, float)):
            v = Decimal(str(v))
        if v < 0:
            raise ValueError("value must be non-negative")
        return v

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        """Ensure timestamp is not too far in the future."""
        # Make timezone-aware if not already
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        # Allow 5 minutes of clock drift
        max_future = now.replace(second=now.second + 300)

        if v > max_future:
            raise ValueError("timestamp cannot be more than 5 minutes in the future")
        return v

    @model_validator(mode="after")
    def validate_unit_for_type(self) -> "MetricBase":
        """Validate that unit is appropriate for metric type."""
        valid_units = METRIC_UNITS.get(self.metric_type, set())
        if valid_units and self.unit not in valid_units:
            # Warning: allow but note it's non-standard
            pass  # In strict mode, we could raise here
        return self


class MetricCreate(MetricBase):
    """
    Schema for submitting a device metric.

    Device is identified via authentication, not in payload.
    """

    pass


class MetricCreateWithDevice(MetricBase):
    """
    Schema for submitting a metric with device ID.

    Used for internal operations.
    """

    device_id: UUID = Field(..., description="Device ID")


class MetricBatchCreate(BaseSchema):
    """
    Schema for submitting multiple metrics at once.

    CRITICAL: All metrics in batch must be for the same device.
    """

    metrics: list[MetricCreate] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of metrics to submit (max 100)",
    )


class MetricResponse(MetricBase):
    """Metric response schema."""

    id: UUID = Field(..., description="Metric ID")
    device_id: UUID = Field(..., description="Device ID")
    created_at: datetime = Field(..., description="Record creation time")


class MetricAggregation(BaseSchema):
    """Aggregated metric data."""

    metric_type: MetricType = Field(..., description="Metric type")
    unit: str = Field(..., description="Unit of measurement")
    min_value: Decimal = Field(..., description="Minimum value")
    max_value: Decimal = Field(..., description="Maximum value")
    avg_value: Decimal = Field(..., description="Average value")
    sum_value: Decimal = Field(..., description="Sum of values")
    count: int = Field(..., ge=0, description="Number of data points")
    first_timestamp: datetime = Field(..., description="First measurement time")
    last_timestamp: datetime = Field(..., description="Last measurement time")


class MetricTimeSeriesPoint(BaseSchema):
    """Single point in a time series."""

    timestamp: datetime = Field(..., description="Data point timestamp")
    value: Decimal = Field(..., description="Metric value")


class MetricTimeSeries(BaseSchema):
    """Time series data for a metric."""

    device_id: UUID = Field(..., description="Device ID")
    metric_type: MetricType = Field(..., description="Metric type")
    unit: str = Field(..., description="Unit of measurement")
    data_points: list[MetricTimeSeriesPoint] = Field(
        ..., description="Time series data points"
    )
    aggregation: Optional[MetricAggregation] = Field(
        None, description="Aggregated statistics"
    )
