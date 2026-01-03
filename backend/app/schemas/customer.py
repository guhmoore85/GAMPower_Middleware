"""
Customer Schemas

Request/response schemas for customer operations.
CRITICAL: Email and phone validation is strict.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.models.transaction import PaymentProvider
from app.schemas.common import BaseSchema, TimestampMixin


class CustomerBase(BaseSchema):
    """Base customer schema with common fields."""

    external_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="External customer identifier from partner system",
    )
    email: Optional[EmailStr] = Field(
        None,
        description="Customer email address",
    )
    phone: Optional[str] = Field(
        None,
        max_length=20,
        pattern=r"^\+[1-9]\d{7,14}$",
        description="Customer phone number in E.164 format (e.g., +12025551234)",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional customer metadata",
    )

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, v: str) -> str:
        """Ensure external_id is not whitespace only."""
        v = v.strip()
        if not v:
            raise ValueError("external_id cannot be empty or whitespace only")
        return v

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        """Normalize phone number format."""
        if v is None:
            return None
        # Remove spaces, dashes, parentheses
        v = "".join(c for c in v if c not in " -().")
        # Ensure starts with +
        if v and not v.startswith("+"):
            v = f"+{v}"
        return v


class CustomerCreate(CustomerBase):
    """
    Schema for creating a new customer.
    """

    payment_provider: PaymentProvider = Field(
        ...,
        description="Preferred payment provider",
    )
    payment_provider_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Customer ID with the payment provider",
    )


class CustomerUpdate(BaseSchema):
    """
    Schema for updating a customer.

    All fields are optional.
    """

    email: Optional[EmailStr] = Field(
        None,
        description="Customer email address",
    )
    phone: Optional[str] = Field(
        None,
        max_length=20,
        pattern=r"^\+[1-9]\d{7,14}$",
        description="Customer phone number in E.164 format",
    )
    payment_provider: Optional[PaymentProvider] = Field(
        None,
        description="Preferred payment provider",
    )
    payment_provider_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Customer ID with the payment provider",
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Additional customer metadata",
    )

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        """Normalize phone number format."""
        if v is None:
            return None
        v = "".join(c for c in v if c not in " -().")
        if v and not v.startswith("+"):
            v = f"+{v}"
        return v


class CustomerResponse(CustomerBase, TimestampMixin):
    """
    Customer response schema.

    Used for GET operations.
    """

    id: UUID = Field(..., description="Customer UUID")
    payment_provider: PaymentProvider = Field(..., description="Payment provider")
    payment_provider_id: Optional[str] = Field(
        None, description="Customer ID with payment provider"
    )


class CustomerWithDevices(CustomerResponse):
    """Customer response with associated devices."""

    device_count: int = Field(..., ge=0, description="Number of associated devices")
    devices: list["CustomerDeviceSummary"] = Field(
        default_factory=list,
        description="Associated devices",
    )


class CustomerDeviceSummary(BaseSchema):
    """Summary of customer's device."""

    id: UUID = Field(..., description="Device ID")
    external_id: str = Field(..., description="Device external ID")
    device_type: str = Field(..., description="Device type")
    status: str = Field(..., description="Device status")


class CustomerTransactionSummary(BaseSchema):
    """Summary of customer's transactions."""

    total_transactions: int = Field(..., ge=0, description="Total transactions")
    total_amount: float = Field(..., ge=0, description="Total amount paid")
    currency: str = Field(..., description="Primary currency")
    last_transaction_at: Optional[datetime] = Field(
        None, description="Last transaction timestamp"
    )


# Update forward references
CustomerWithDevices.model_rebuild()
