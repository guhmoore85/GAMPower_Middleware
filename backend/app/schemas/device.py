"""
Device Schemas

Request/response schemas for device operations.
CRITICAL: All validation errors are detailed.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.models.device import DeviceStatus, DeviceType
from app.schemas.common import BaseSchema, TimestampMixin


class DeviceBase(BaseSchema):
    """Base device schema with common fields."""

    external_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[\w\-]+$",
        description="External device identifier (alphanumeric, hyphens, underscores)",
    )
    device_type: DeviceType = Field(
        ...,
        description="Type of device (solar, emobility)",
    )
    manufacturer: Optional[str] = Field(
        None,
        max_length=100,
        description="Device manufacturer",
    )
    model: Optional[str] = Field(
        None,
        max_length=100,
        description="Device model",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional device metadata",
    )

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, v: str) -> str:
        """Ensure external_id is not whitespace only."""
        if not v.strip():
            raise ValueError("external_id cannot be empty or whitespace only")
        return v.strip()


class DeviceCreate(DeviceBase):
    """
    Schema for creating a new device.

    Returns device_id and secret_key (shown only once).
    """

    customer_id: Optional[UUID] = Field(
        None,
        description="Associated customer ID (optional)",
    )


class DeviceUpdate(BaseSchema):
    """
    Schema for updating a device.

    All fields are optional.
    """

    manufacturer: Optional[str] = Field(
        None,
        max_length=100,
        description="Device manufacturer",
    )
    model: Optional[str] = Field(
        None,
        max_length=100,
        description="Device model",
    )
    status: Optional[DeviceStatus] = Field(
        None,
        description="Device status",
    )
    customer_id: Optional[UUID] = Field(
        None,
        description="Associated customer ID",
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Additional device metadata",
    )


class DeviceResponse(DeviceBase, TimestampMixin):
    """
    Device response schema.

    Used for GET operations.
    """

    id: UUID = Field(..., description="Device UUID")
    status: DeviceStatus = Field(..., description="Current device status")
    customer_id: Optional[UUID] = Field(None, description="Associated customer ID")

    # Note: openpaygo_secret_key is NEVER returned after creation


class DeviceCreateResponse(DeviceResponse):
    """
    Device creation response.

    CRITICAL: secret_key is only shown once!
    """

    secret_key: str = Field(
        ...,
        description="OpenPAYGO secret key - SAVE THIS, shown only once!",
    )


class DeviceWithMetrics(DeviceResponse):
    """Device response with recent metrics."""

    latest_metrics: list["MetricSummary"] = Field(
        default_factory=list,
        description="Latest metrics by type",
    )
    active_activation: Optional["ActivationSummary"] = Field(
        None,
        description="Current active activation",
    )


class MetricSummary(BaseSchema):
    """Summary of latest metric."""

    metric_type: str = Field(..., description="Type of metric")
    value: float = Field(..., description="Metric value")
    unit: str = Field(..., description="Unit of measurement")
    timestamp: datetime = Field(..., description="Measurement timestamp")


class ActivationSummary(BaseSchema):
    """Summary of activation."""

    id: UUID = Field(..., description="Activation ID")
    activation_type: str = Field(..., description="Type of activation")
    expires_at: Optional[datetime] = Field(None, description="Expiration time")
    is_active: bool = Field(..., description="Whether activation is active")


class DeviceActivateRequest(BaseSchema):
    """Request to manually activate a device."""

    days_valid: int = Field(
        30,
        ge=1,
        le=365,
        description="Number of days the activation is valid",
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Reason for manual activation (required for audit)",
    )


class DeviceActivateResponse(BaseSchema):
    """Response after activating a device."""

    activation_id: UUID = Field(..., description="Activation record ID")
    activation_token: str = Field(..., description="OpenPAYGO activation token")
    expires_at: Optional[datetime] = Field(None, description="Token expiration time")


class DeviceSuspendRequest(BaseSchema):
    """Request to suspend a device."""

    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Reason for suspension (required for audit)",
    )


class DeviceTokenRequest(BaseSchema):
    """Request for device token generation."""

    days_valid: int = Field(
        30,
        ge=1,
        le=365,
        description="Number of days the token is valid",
    )


class DeviceTokenResponse(BaseSchema):
    """Response with generated token."""

    token: str = Field(..., description="Generated OpenPAYGO token")
    expires_at: datetime = Field(..., description="Token expiration time")
    days_valid: int = Field(..., description="Days of validity")


# Update forward references
DeviceWithMetrics.model_rebuild()
