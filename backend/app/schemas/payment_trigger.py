"""
Payment Trigger Schemas

Request/response schemas for payment triggers.
CRITICAL: Config validation is based on trigger type.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.models.device_metric import MetricType
from app.models.payment_trigger import TriggerType
from app.schemas.common import BaseSchema


class UsageThresholdConfig(BaseSchema):
    """Configuration for usage threshold triggers."""

    metric_type: MetricType = Field(
        ..., description="Metric type to monitor"
    )
    threshold: Decimal = Field(
        ...,
        gt=0,
        description="Threshold value that triggers payment",
    )
    amount: Optional[Decimal] = Field(
        None,
        gt=0,
        description="Payment amount (if not set, uses default)",
    )
    currency: Optional[str] = Field(
        None,
        min_length=3,
        max_length=3,
        description="Currency code (if not set, uses customer's default)",
    )


class TimeBasedConfig(BaseSchema):
    """Configuration for time-based triggers."""

    interval_days: int = Field(
        ...,
        ge=1,
        le=365,
        description="Trigger interval in days",
    )
    amount: Optional[Decimal] = Field(
        None,
        gt=0,
        description="Payment amount (if not set, uses default)",
    )
    currency: Optional[str] = Field(
        None,
        min_length=3,
        max_length=3,
        description="Currency code",
    )
    start_date: Optional[datetime] = Field(
        None,
        description="When to start the trigger schedule",
    )


class ManualTriggerConfig(BaseSchema):
    """Configuration for manual triggers."""

    amount: Optional[Decimal] = Field(
        None,
        gt=0,
        description="Default payment amount",
    )
    currency: Optional[str] = Field(
        None,
        min_length=3,
        max_length=3,
        description="Currency code",
    )
    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Notes about the manual trigger",
    )


class TriggerBase(BaseSchema):
    """Base trigger schema."""

    trigger_type: TriggerType = Field(..., description="Type of trigger")
    config: dict[str, Any] = Field(..., description="Trigger configuration")
    is_active: bool = Field(True, description="Whether trigger is active")


class TriggerCreate(TriggerBase):
    """
    Schema for creating a trigger.
    """

    device_id: UUID = Field(..., description="Device ID")

    @model_validator(mode="after")
    def validate_config_for_type(self) -> "TriggerCreate":
        """Validate config matches trigger type."""
        errors = []

        if self.trigger_type == TriggerType.USAGE_THRESHOLD:
            if "metric_type" not in self.config:
                errors.append("metric_type is required for usage_threshold triggers")
            if "threshold" not in self.config:
                errors.append("threshold is required for usage_threshold triggers")
            elif self.config["threshold"] <= 0:
                errors.append("threshold must be positive")

        elif self.trigger_type == TriggerType.TIME_BASED:
            if "interval_days" not in self.config:
                errors.append("interval_days is required for time_based triggers")
            elif not (1 <= self.config["interval_days"] <= 365):
                errors.append("interval_days must be between 1 and 365")

        # Validate amount if present
        if "amount" in self.config and self.config["amount"] <= 0:
            errors.append("amount must be positive")

        if errors:
            raise ValueError("; ".join(errors))

        return self


class TriggerUpdate(BaseSchema):
    """
    Schema for updating a trigger.
    """

    config: Optional[dict[str, Any]] = Field(
        None, description="Updated configuration"
    )
    is_active: Optional[bool] = Field(
        None, description="Active status"
    )
    last_triggered_at: Optional[datetime] = Field(
        None, description="Override last triggered time (for testing)"
    )


class TriggerResponse(TriggerBase):
    """Trigger response schema."""

    id: UUID = Field(..., description="Trigger ID")
    device_id: UUID = Field(..., description="Device ID")
    last_triggered_at: Optional[datetime] = Field(
        None, description="Last trigger time"
    )
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class TriggerEvaluationResult(BaseSchema):
    """Result of trigger evaluation."""

    device_id: UUID = Field(..., description="Device ID")
    trigger_id: UUID = Field(..., description="Trigger ID")
    trigger_type: TriggerType = Field(..., description="Trigger type")
    triggered: bool = Field(..., description="Whether trigger fired")
    reason: str = Field(..., description="Evaluation reason/result")
    current_value: Optional[Decimal] = Field(
        None, description="Current metric value (for usage triggers)"
    )
    threshold: Optional[Decimal] = Field(
        None, description="Threshold value (for usage triggers)"
    )
    action_taken: Optional[str] = Field(
        None, description="Action taken if triggered"
    )
    payment_created: bool = Field(
        False, description="Whether payment was created"
    )
    transaction_id: Optional[UUID] = Field(
        None, description="Created transaction ID"
    )


class TriggerEvaluationRequest(BaseSchema):
    """Request to manually evaluate triggers."""

    device_id: UUID = Field(..., description="Device to evaluate triggers for")
    trigger_types: Optional[list[TriggerType]] = Field(
        None, description="Specific trigger types to evaluate (all if not set)"
    )
    dry_run: bool = Field(
        False, description="If true, don't create payments"
    )
