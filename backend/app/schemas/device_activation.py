"""
Device Activation Schemas

Request/response schemas for device activations.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.device_activation import ActivationType
from app.schemas.common import BaseSchema


class ActivationBase(BaseSchema):
    """Base activation schema."""

    activation_type: ActivationType = Field(
        ..., description="Type of activation"
    )
    expires_at: Optional[datetime] = Field(
        None, description="When the activation expires"
    )


class ActivationCreate(ActivationBase):
    """
    Schema for creating an activation.

    Usually created internally after payment or by admin.
    """

    device_id: UUID = Field(..., description="Device to activate")
    transaction_id: Optional[UUID] = Field(
        None, description="Related transaction (required for payment type)"
    )
    activation_token: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="OpenPAYGO activation token",
    )


class ActivationResponse(ActivationBase):
    """Activation response schema."""

    id: UUID = Field(..., description="Activation ID")
    device_id: UUID = Field(..., description="Device ID")
    transaction_id: Optional[UUID] = Field(None, description="Transaction ID")
    activation_token: str = Field(..., description="Activation token")
    activated_at: Optional[datetime] = Field(
        None, description="When device confirmed activation"
    )
    created_at: datetime = Field(..., description="Record creation time")
    is_expired: bool = Field(..., description="Whether activation has expired")
    is_active: bool = Field(..., description="Whether activation is currently active")


class ActivationWithDevice(ActivationResponse):
    """Activation response with device details."""

    device_external_id: str = Field(..., description="Device external ID")
    device_type: str = Field(..., description="Device type")
    device_status: str = Field(..., description="Device status")


class ManualActivationRequest(BaseSchema):
    """Request for manual device activation."""

    device_id: UUID = Field(..., description="Device to activate")
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
        description="Reason for manual activation (audit trail)",
    )


class TrialActivationRequest(BaseSchema):
    """Request for trial activation."""

    device_id: UUID = Field(..., description="Device to activate")
    days_valid: int = Field(
        7,
        ge=1,
        le=30,
        description="Trial duration in days (max 30)",
    )
    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Notes about the trial",
    )
