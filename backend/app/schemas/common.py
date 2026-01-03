"""
Common Schemas

Shared schemas for error responses, pagination, and health checks.
"""

from datetime import datetime
from typing import Any, Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Generic type for paginated responses
T = TypeVar("T")


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(
        from_attributes=True,  # Allow ORM model conversion
        str_strip_whitespace=True,  # Strip whitespace from strings
        validate_assignment=True,  # Validate on assignment
        extra="forbid",  # Forbid extra fields
    )


class ErrorDetail(BaseSchema):
    """Detailed error information."""

    field: Optional[str] = Field(None, description="Field that caused the error")
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code")


class ErrorResponse(BaseSchema):
    """
    Standard error response format.

    CRITICAL: All API errors return this format.
    """

    error_code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Error timestamp",
    )
    request_id: Optional[str] = Field(None, description="Request ID for tracing")
    details: Optional[dict[str, Any]] = Field(None, description="Additional error details")
    field_errors: Optional[list[ErrorDetail]] = Field(
        None, description="Field-level validation errors"
    )


class HealthComponentStatus(BaseSchema):
    """Health status for individual component."""

    status: str = Field(..., description="Component status (healthy/unhealthy)")
    latency_ms: Optional[float] = Field(None, description="Response latency in ms")
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class HealthResponse(BaseSchema):
    """
    Health check response.

    CRITICAL: Used by load balancers and monitoring.
    """

    status: str = Field(..., description="Overall status (healthy/degraded/unhealthy)")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Deployment environment")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Health check timestamp",
    )
    components: dict[str, HealthComponentStatus] = Field(
        default_factory=dict,
        description="Individual component status",
    )


class PaginationMeta(BaseSchema):
    """Pagination metadata."""

    total: int = Field(..., ge=0, description="Total number of items")
    page: int = Field(..., ge=1, description="Current page number")
    page_size: int = Field(..., ge=1, le=100, description="Items per page")
    total_pages: int = Field(..., ge=0, description="Total number of pages")
    has_next: bool = Field(..., description="Whether there are more pages")
    has_prev: bool = Field(..., description="Whether there are previous pages")


class PaginatedResponse(BaseSchema, Generic[T]):
    """
    Paginated response wrapper.

    CRITICAL: All list endpoints return this format.
    """

    data: list[T] = Field(..., description="List of items")
    meta: PaginationMeta = Field(..., description="Pagination metadata")

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedResponse[T]":
        """Create a paginated response."""
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

        return cls(
            data=items,
            meta=PaginationMeta(
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
        )


class SuccessResponse(BaseSchema):
    """Generic success response."""

    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")
    data: Optional[dict[str, Any]] = Field(None, description="Response data")


class IdResponse(BaseSchema):
    """Response containing just an ID."""

    id: UUID = Field(..., description="Created/updated resource ID")


class TimestampMixin(BaseSchema):
    """Mixin for timestamp fields."""

    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class WebhookEventBase(BaseSchema):
    """Base schema for webhook events."""

    event_type: str = Field(..., description="Webhook event type")
    timestamp: datetime = Field(..., description="Event timestamp")
    provider: str = Field(..., description="Payment provider")


class AnalyticsOverview(BaseSchema):
    """Analytics overview data."""

    total_devices: int = Field(..., ge=0, description="Total registered devices")
    active_devices: int = Field(..., ge=0, description="Currently active devices")
    total_customers: int = Field(..., ge=0, description="Total registered customers")
    total_revenue: float = Field(..., ge=0, description="Total revenue")
    payment_success_rate: float = Field(
        ..., ge=0, le=100, description="Payment success rate percentage"
    )
    today_transactions: int = Field(..., ge=0, description="Transactions today")
    yesterday_transactions: int = Field(..., ge=0, description="Transactions yesterday")


class RevenueData(BaseSchema):
    """Revenue data point."""

    period: str = Field(..., description="Time period label")
    amount: float = Field(..., ge=0, description="Revenue amount")
    currency: str = Field(..., description="Currency code")
    transaction_count: int = Field(..., ge=0, description="Number of transactions")


class DeviceUsageData(BaseSchema):
    """Device usage data point."""

    timestamp: datetime = Field(..., description="Data point timestamp")
    device_type: str = Field(..., description="Device type")
    metric_type: str = Field(..., description="Metric type")
    total_value: float = Field(..., ge=0, description="Aggregated value")
    device_count: int = Field(..., ge=0, description="Number of devices")
