"""
OpenPAYGO API Endpoints

Handles:
- Device registration
- Device metrics ingestion
- Token generation (v2 algorithm)
- Token validation
- Device activation

CRITICAL: All operations are logged with request_id.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    ApiKeyDep,
    DbSessionDep,
    PaginationDep,
    RequestIdDep,
)
from app.core.exceptions import (
    DeviceNotFoundError,
    InvalidTokenError,
    TokenExpiredError,
    TransactionNotFoundError,
)
from app.core.logging import get_logger
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_metric import DeviceMetric, MetricType
from app.models.device_token import DeviceToken
from app.schemas.common import ErrorResponse, PaginatedResponse
from app.schemas.device import (
    DeviceCreate,
    DeviceCreateResponse,
    DeviceResponse,
    DeviceTokenRequest,
    DeviceTokenResponse,
)
from app.schemas.device_metric import (
    MetricBatchCreate,
    MetricCreate,
    MetricResponse,
)
from app.services.openpaygo_service import OpenPAYGOService
from app.services.token_service import OpenPAYGOTokenService, TokenType

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# SCHEMAS FOR TOKEN ENDPOINTS
# =============================================================================


class TokenFromTransactionRequest(BaseModel):
    """Request to generate token from transaction."""
    transaction_id: UUID = Field(..., description="Transaction UUID")
    price_per_day: Optional[Decimal] = Field(
        None,
        description="Price per day of access (default: $1.00)",
        ge=Decimal("0.01"),
    )


class TokenGenerateRequest(BaseModel):
    """Request to generate token with specific days."""
    days_valid: int = Field(..., ge=1, le=365, description="Number of days")
    token_type: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Token type: 1=ADD_TIME, 2=SET_TIME, 3=DISABLE_PAYG, 4=COUNTER_SYNC",
    )
    transaction_id: Optional[UUID] = Field(None, description="Related transaction (optional)")


class TokenValidateRequest(BaseModel):
    """Request to validate a token."""
    token: str = Field(..., description="Token in XXX-XXX-XXX format", pattern=r"^\d{3}-\d{3}-\d{3}$")
    mark_as_used: bool = Field(default=True, description="Whether to mark token as used if valid")


class TokenResponse(BaseModel):
    """Response with generated token details."""
    token: str = Field(..., description="Token in XXX-XXX-XXX format")
    token_id: UUID = Field(..., description="Token record UUID")
    device_id: UUID = Field(..., description="Device UUID")
    expires_at: datetime = Field(..., description="Token expiration timestamp")
    days_added: int = Field(..., description="Number of days this token adds")
    counter_value: int = Field(..., description="OpenPAYGO counter value")
    token_type: int = Field(..., description="Token type")
    token_type_name: str = Field(..., description="Human-readable token type")
    transaction_id: Optional[UUID] = Field(None, description="Related transaction")


class TokenValidationResponse(BaseModel):
    """Response for token validation."""
    is_valid: bool = Field(..., description="Whether token is valid")
    token_id: Optional[UUID] = Field(None, description="Token record UUID if found")
    device_id: UUID = Field(..., description="Device UUID")
    days_added: int = Field(default=0, description="Days this token adds")
    expires_at: Optional[datetime] = Field(None, description="Token expiration")
    error_message: Optional[str] = Field(None, description="Error message if invalid")
    was_already_used: bool = Field(default=False, description="Whether token was already used")


class DeviceTokenListResponse(BaseModel):
    """Response with list of device tokens."""
    token_id: UUID
    token_type: int
    token_type_name: str
    days_added: int
    counter_value: int
    generated_at: datetime
    expires_at: datetime
    is_used: bool
    used_at: Optional[datetime]
    is_expired: bool
    transaction_id: Optional[UUID]


# =============================================================================
# DEVICE ENDPOINTS
# =============================================================================


@router.post(
    "/device/register",
    response_model=DeviceCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        409: {"model": ErrorResponse, "description": "Device already exists"},
    },
)
async def register_device(
    device_data: DeviceCreate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
):
    """
    Register a new device.

    CRITICAL: The secret_key is only returned once!
    Store it securely - it cannot be retrieved again.

    Returns:
    - device_id: UUID of the created device
    - secret_key: OpenPAYGO secret key (SAVE THIS!)
    """
    logger.info(
        "Device registration request",
        external_id=device_data.external_id,
        device_type=device_data.device_type.value,
        request_id=request_id,
    )

    # Check if device already exists
    existing = await db.execute(
        select(Device).where(Device.external_id == device_data.external_id)
    )
    if existing.scalar_one_or_none():
        logger.warning(
            "Device registration failed - already exists",
            external_id=device_data.external_id,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "DEVICE_EXISTS",
                "message": f"Device with external_id '{device_data.external_id}' already exists",
                "request_id": request_id,
            },
        )

    service = OpenPAYGOService(db)

    try:
        device, secret_key = await service.register_device(
            external_id=device_data.external_id,
            device_type=device_data.device_type.value,
            manufacturer=device_data.manufacturer,
            model=device_data.model,
            customer_id=device_data.customer_id,
            metadata=device_data.metadata,
        )

        await db.commit()

        logger.info(
            "Device registered successfully",
            device_id=str(device.id),
            external_id=device.external_id,
            request_id=request_id,
        )

        return DeviceCreateResponse(
            id=device.id,
            external_id=device.external_id,
            device_type=device.device_type,
            manufacturer=device.manufacturer,
            model=device.model,
            status=device.status,
            customer_id=device.customer_id,
            metadata=device.metadata_,
            created_at=device.created_at,
            updated_at=device.updated_at,
            secret_key=secret_key,
        )

    except Exception as e:
        logger.error(
            "Device registration failed",
            external_id=device_data.external_id,
            error=str(e),
            request_id=request_id,
        )
        await db.rollback()
        raise


@router.get(
    "/device/{device_id}",
    response_model=DeviceResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def get_device(
    device_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
):
    """
    Get device by ID.
    """
    logger.debug(
        "Get device request",
        device_id=str(device_id),
        request_id=request_id,
    )

    result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise DeviceNotFoundError(device_id, request_id=request_id)

    return DeviceResponse(
        id=device.id,
        external_id=device.external_id,
        device_type=device.device_type,
        manufacturer=device.manufacturer,
        model=device.model,
        status=device.status,
        customer_id=device.customer_id,
        metadata=device.metadata_,
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


# =============================================================================
# TOKEN GENERATION ENDPOINTS (OpenPAYGO v2)
# =============================================================================


@router.get(
    "/device/{device_id}/token",
    response_model=TokenResponse,
    responses={
        403: {"model": ErrorResponse, "description": "Device suspended"},
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def generate_device_token(
    device_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
    days_valid: int = Query(30, ge=1, le=365, description="Token validity in days"),
):
    """
    Generate activation token for device using OpenPAYGO v2 algorithm.

    The token can be used to activate the device for the specified number of days.
    Uses ADD_TIME token type by default.
    """
    logger.info(
        "Token generation request (GET)",
        device_id=str(device_id),
        days_valid=days_valid,
        request_id=request_id,
    )

    service = OpenPAYGOTokenService(db)

    try:
        result = await service.generate_token(
            device_id=device_id,
            days_valid=days_valid,
            token_type=TokenType.ADD_TIME,
        )

        await db.commit()

        logger.info(
            "Token generated successfully",
            device_id=str(device_id),
            token_id=str(result.token_id),
            expires_at=result.expires_at.isoformat(),
            request_id=request_id,
        )

        return TokenResponse(
            token=result.token,
            token_id=result.token_id,
            device_id=result.device_id,
            expires_at=result.expires_at,
            days_added=result.days_added,
            counter_value=result.counter_value,
            token_type=result.token_type,
            token_type_name=_get_token_type_name(result.token_type),
            transaction_id=result.transaction_id,
        )

    except (DeviceNotFoundError, InvalidTokenError):
        raise
    except Exception as e:
        logger.error(
            "Token generation failed",
            device_id=str(device_id),
            error=str(e),
            request_id=request_id,
        )
        await db.rollback()
        raise


@router.post(
    "/device/{device_id}/token",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        403: {"model": ErrorResponse, "description": "Device suspended"},
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def generate_device_token_advanced(
    device_id: UUID,
    request_data: TokenGenerateRequest,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
):
    """
    Generate activation token with advanced options.

    Supports:
    - Custom token types (ADD_TIME, SET_TIME, DISABLE_PAYG, COUNTER_SYNC)
    - Linking to a transaction
    """
    logger.info(
        "Token generation request (POST)",
        device_id=str(device_id),
        days_valid=request_data.days_valid,
        token_type=request_data.token_type,
        transaction_id=str(request_data.transaction_id) if request_data.transaction_id else None,
        request_id=request_id,
    )

    service = OpenPAYGOTokenService(db)

    try:
        result = await service.generate_token(
            device_id=device_id,
            days_valid=request_data.days_valid,
            token_type=request_data.token_type,
            transaction_id=request_data.transaction_id,
        )

        await db.commit()

        logger.info(
            "Token generated successfully",
            device_id=str(device_id),
            token_id=str(result.token_id),
            request_id=request_id,
        )

        return TokenResponse(
            token=result.token,
            token_id=result.token_id,
            device_id=result.device_id,
            expires_at=result.expires_at,
            days_added=result.days_added,
            counter_value=result.counter_value,
            token_type=result.token_type,
            token_type_name=_get_token_type_name(result.token_type),
            transaction_id=result.transaction_id,
        )

    except (DeviceNotFoundError, InvalidTokenError, TransactionNotFoundError):
        raise
    except Exception as e:
        logger.error(
            "Token generation failed",
            device_id=str(device_id),
            error=str(e),
            request_id=request_id,
        )
        await db.rollback()
        raise


@router.post(
    "/device/{device_id}/token/from-transaction",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        403: {"model": ErrorResponse, "description": "Device suspended"},
        404: {"model": ErrorResponse, "description": "Device or transaction not found"},
    },
)
async def generate_token_from_transaction(
    device_id: UUID,
    request_data: TokenFromTransactionRequest,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
):
    """
    Generate token based on transaction amount.

    Calculates days from transaction amount using price_per_day.
    E.g., $10 transaction with $1/day = 10 days of access.
    """
    logger.info(
        "Token generation from transaction",
        device_id=str(device_id),
        transaction_id=str(request_data.transaction_id),
        price_per_day=float(request_data.price_per_day) if request_data.price_per_day else None,
        request_id=request_id,
    )

    service = OpenPAYGOTokenService(db)

    try:
        result = await service.generate_token_from_transaction(
            device_id=device_id,
            transaction_id=request_data.transaction_id,
            price_per_day=request_data.price_per_day,
        )

        await db.commit()

        logger.info(
            "Token generated from transaction successfully",
            device_id=str(device_id),
            token_id=str(result.token_id),
            days_added=result.days_added,
            request_id=request_id,
        )

        return TokenResponse(
            token=result.token,
            token_id=result.token_id,
            device_id=result.device_id,
            expires_at=result.expires_at,
            days_added=result.days_added,
            counter_value=result.counter_value,
            token_type=result.token_type,
            token_type_name=_get_token_type_name(result.token_type),
            transaction_id=result.transaction_id,
        )

    except (DeviceNotFoundError, TransactionNotFoundError):
        raise
    except Exception as e:
        logger.error(
            "Token generation from transaction failed",
            device_id=str(device_id),
            transaction_id=str(request_data.transaction_id),
            error=str(e),
            request_id=request_id,
        )
        await db.rollback()
        raise


@router.post(
    "/device/{device_id}/validate",
    response_model=TokenValidationResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid token"},
        404: {"model": ErrorResponse, "description": "Device not found"},
        410: {"model": ErrorResponse, "description": "Token expired"},
    },
)
async def validate_device_token(
    device_id: UUID,
    request_data: TokenValidateRequest,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
):
    """
    Validate a token for a device.

    Checks if the token is:
    - Correctly formatted
    - Valid for this device
    - Not already used
    - Not expired

    Optionally marks the token as used (default: True).
    """
    logger.info(
        "Token validation request",
        device_id=str(device_id),
        mark_as_used=request_data.mark_as_used,
        request_id=request_id,
    )

    service = OpenPAYGOTokenService(db)

    try:
        result = await service.validate_token(
            device_id=device_id,
            token=request_data.token,
            mark_as_used=request_data.mark_as_used,
        )

        if request_data.mark_as_used and result.is_valid:
            await db.commit()

        logger.info(
            "Token validation completed",
            device_id=str(device_id),
            is_valid=result.is_valid,
            token_id=str(result.token_id) if result.token_id else None,
            request_id=request_id,
        )

        return TokenValidationResponse(
            is_valid=result.is_valid,
            token_id=result.token_id,
            device_id=device_id,
            days_added=result.days_added,
            expires_at=result.expires_at,
            error_message=result.error_message,
            was_already_used=result.was_already_used,
        )

    except TokenExpiredError as e:
        return TokenValidationResponse(
            is_valid=False,
            device_id=device_id,
            error_message=str(e.message),
        )
    except InvalidTokenError as e:
        return TokenValidationResponse(
            is_valid=False,
            device_id=device_id,
            error_message=e.reason,
        )
    except DeviceNotFoundError:
        raise
    except Exception as e:
        logger.error(
            "Token validation error",
            device_id=str(device_id),
            error=str(e),
            request_id=request_id,
        )
        raise


@router.get(
    "/device/{device_id}/tokens",
    response_model=list[DeviceTokenListResponse],
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def get_device_tokens(
    device_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
    include_used: bool = Query(False, description="Include used tokens"),
    include_expired: bool = Query(False, description="Include expired tokens"),
    limit: int = Query(50, ge=1, le=200, description="Maximum tokens to return"),
):
    """
    Get tokens for a device.

    By default, only returns active (unused, not expired) tokens.
    """
    logger.debug(
        "Get device tokens",
        device_id=str(device_id),
        include_used=include_used,
        include_expired=include_expired,
        limit=limit,
        request_id=request_id,
    )

    service = OpenPAYGOTokenService(db)

    try:
        tokens = await service.get_device_tokens(
            device_id=device_id,
            include_used=include_used,
            include_expired=include_expired,
            limit=limit,
        )

        return [
            DeviceTokenListResponse(
                token_id=t.id,
                token_type=t.token_type,
                token_type_name=t.token_type_name,
                days_added=t.days_added,
                counter_value=t.counter_value,
                generated_at=t.generated_at,
                expires_at=t.expires_at,
                is_used=t.is_used,
                used_at=t.used_at,
                is_expired=t.is_expired(),
                transaction_id=t.transaction_id,
            )
            for t in tokens
        ]

    except DeviceNotFoundError:
        raise


# =============================================================================
# METRICS ENDPOINTS
# =============================================================================


@router.post(
    "/metrics",
    response_model=MetricResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def submit_metric(
    metric_data: MetricCreate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
    device_id: UUID = Query(..., description="Device ID"),
):
    """
    Submit a device metric.

    Requires device_id query parameter for device identification.
    In production, use device API key for authentication.
    """
    logger.info(
        "Metric submission",
        device_id=str(device_id),
        metric_type=metric_data.metric_type.value,
        value=float(metric_data.value),
        request_id=request_id,
    )

    # Verify device exists
    result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        logger.error(
            "Metric submission failed - device not found",
            device_id=str(device_id),
            request_id=request_id,
        )
        raise DeviceNotFoundError(device_id, request_id=request_id)

    # Create metric
    metric = DeviceMetric(
        device_id=device_id,
        metric_type=metric_data.metric_type,
        value=metric_data.value,
        unit=metric_data.unit,
        timestamp=metric_data.timestamp,
        metadata_=metric_data.metadata or {},
    )

    db.add(metric)
    await db.commit()
    await db.refresh(metric)

    logger.info(
        "Metric recorded",
        metric_id=str(metric.id),
        device_id=str(device_id),
        metric_type=metric.metric_type.value,
        request_id=request_id,
    )

    return MetricResponse(
        id=metric.id,
        device_id=metric.device_id,
        metric_type=metric.metric_type,
        value=metric.value,
        unit=metric.unit,
        timestamp=metric.timestamp,
        metadata=metric.metadata_,
        created_at=metric.created_at,
    )


@router.post(
    "/metrics/batch",
    response_model=list[MetricResponse],
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def submit_metrics_batch(
    batch_data: MetricBatchCreate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
    device_id: UUID = Query(..., description="Device ID"),
):
    """
    Submit multiple metrics at once (max 100).

    All metrics must be for the same device.
    """
    logger.info(
        "Batch metric submission",
        device_id=str(device_id),
        count=len(batch_data.metrics),
        request_id=request_id,
    )

    # Verify device exists
    result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise DeviceNotFoundError(device_id, request_id=request_id)

    # Create metrics
    metrics = []
    for metric_data in batch_data.metrics:
        metric = DeviceMetric(
            device_id=device_id,
            metric_type=metric_data.metric_type,
            value=metric_data.value,
            unit=metric_data.unit,
            timestamp=metric_data.timestamp,
            metadata_=metric_data.metadata or {},
        )
        db.add(metric)
        metrics.append(metric)

    await db.commit()

    # Refresh all metrics
    for metric in metrics:
        await db.refresh(metric)

    logger.info(
        "Batch metrics recorded",
        device_id=str(device_id),
        count=len(metrics),
        request_id=request_id,
    )

    return [
        MetricResponse(
            id=m.id,
            device_id=m.device_id,
            metric_type=m.metric_type,
            value=m.value,
            unit=m.unit,
            timestamp=m.timestamp,
            metadata=m.metadata_,
            created_at=m.created_at,
        )
        for m in metrics
    ]


@router.get(
    "/device/{device_id}/metrics",
    response_model=list[MetricResponse],
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def get_device_metrics(
    device_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    api_key: ApiKeyDep,
    metric_type: Optional[MetricType] = Query(None, description="Filter by metric type"),
    since: Optional[datetime] = Query(None, description="Filter metrics since timestamp"),
    limit: int = Query(100, ge=1, le=1000, description="Max metrics to return"),
):
    """
    Get metrics for a device.
    """
    logger.debug(
        "Get device metrics",
        device_id=str(device_id),
        metric_type=metric_type.value if metric_type else None,
        limit=limit,
        request_id=request_id,
    )

    # Build query
    query = select(DeviceMetric).where(DeviceMetric.device_id == device_id)

    if metric_type:
        query = query.where(DeviceMetric.metric_type == metric_type)

    if since:
        query = query.where(DeviceMetric.timestamp >= since)

    query = query.order_by(DeviceMetric.timestamp.desc()).limit(limit)

    result = await db.execute(query)
    metrics = result.scalars().all()

    return [
        MetricResponse(
            id=m.id,
            device_id=m.device_id,
            metric_type=m.metric_type,
            value=m.value,
            unit=m.unit,
            timestamp=m.timestamp,
            metadata=m.metadata_,
            created_at=m.created_at,
        )
        for m in metrics
    ]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_token_type_name(token_type: int) -> str:
    """Get human-readable token type name."""
    names = {
        1: "ADD_TIME",
        2: "SET_TIME",
        3: "DISABLE_PAYG",
        4: "COUNTER_SYNC",
    }
    return names.get(token_type, "UNKNOWN")
