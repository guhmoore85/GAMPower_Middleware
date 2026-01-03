"""
OpenPAYGO API Endpoints

Handles:
- Device registration
- Device metrics ingestion
- Token generation
- Device activation

CRITICAL: All operations are logged with request_id.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    ApiKeyDep,
    DbSessionDep,
    PaginationDep,
    RequestIdDep,
)
from app.core.exceptions import DeviceNotFoundError, InvalidTokenError
from app.core.logging import get_logger
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_metric import DeviceMetric, MetricType
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

router = APIRouter()
logger = get_logger(__name__)


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


@router.get(
    "/device/{device_id}/token",
    response_model=DeviceTokenResponse,
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
    Generate activation token for device.

    The token can be used to activate the device.
    """
    logger.info(
        "Token generation request",
        device_id=str(device_id),
        days_valid=days_valid,
        request_id=request_id,
    )

    service = OpenPAYGOService(db)

    try:
        token, expires_at = await service.generate_token(
            device_id=device_id,
            days_valid=days_valid,
        )

        logger.info(
            "Token generated successfully",
            device_id=str(device_id),
            expires_at=expires_at.isoformat(),
            request_id=request_id,
        )

        return DeviceTokenResponse(
            token=token,
            expires_at=expires_at,
            days_valid=days_valid,
        )

    except DeviceNotFoundError:
        raise
    except Exception as e:
        logger.error(
            "Token generation failed",
            device_id=str(device_id),
            error=str(e),
            request_id=request_id,
        )
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
