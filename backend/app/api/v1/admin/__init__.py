"""
Admin Dashboard API Endpoints

Provides:
- Device management
- Transaction viewing
- Analytics overview
- Manual device operations

CRITICAL: All endpoints require admin authentication.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import (
    AdminAuthDep,
    DbSessionDep,
    PaginationDep,
    RequestIdDep,
)
from app.core.exceptions import DeviceNotFoundError
from app.core.logging import audit_logger, get_logger
from app.models.customer import Customer
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_activation import ActivationType, DeviceActivation
from app.models.device_metric import DeviceMetric
from app.models.transaction import Transaction, TransactionStatus
from app.schemas.common import (
    AnalyticsOverview,
    DeviceUsageData,
    ErrorResponse,
    PaginatedResponse,
    RevenueData,
)
from app.schemas.customer import (
    CustomerCreate,
    CustomerResponse,
    CustomerUpdate,
    CustomerWithDevices,
    CustomerDeviceSummary,
)
from app.schemas.device import (
    DeviceActivateRequest,
    DeviceActivateResponse,
    DeviceResponse,
    DeviceSuspendRequest,
    DeviceWithMetrics,
    MetricSummary,
)
from app.schemas.transaction import TransactionResponse, TransactionWithDetails
from app.services.openpaygo_service import OpenPAYGOService

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# DEVICE MANAGEMENT
# =============================================================================


@router.get(
    "/devices",
    response_model=PaginatedResponse[DeviceResponse],
)
async def list_devices(
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
    pagination: PaginationDep,
    status_filter: Optional[DeviceStatus] = Query(None, alias="status"),
    device_type: Optional[DeviceType] = Query(None),
):
    """
    List all devices with pagination.
    """
    logger.debug(
        "List devices",
        page=pagination.page,
        page_size=pagination.page_size,
        status_filter=status_filter.value if status_filter else None,
        device_type=device_type.value if device_type else None,
        request_id=request_id,
    )

    # Build query
    query = select(Device)

    if status_filter:
        query = query.where(Device.status == status_filter)

    if device_type:
        query = query.where(Device.device_type == device_type)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated results
    query = query.offset(pagination.offset).limit(pagination.page_size)
    query = query.order_by(Device.created_at.desc())

    result = await db.execute(query)
    devices = result.scalars().all()

    items = [
        DeviceResponse(
            id=d.id,
            external_id=d.external_id,
            device_type=d.device_type,
            manufacturer=d.manufacturer,
            model=d.model,
            status=d.status,
            customer_id=d.customer_id,
            metadata=d.metadata_,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in devices
    ]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/devices/{device_id}",
    response_model=DeviceWithMetrics,
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def get_device_details(
    device_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Get device details including recent metrics.
    """
    logger.debug(
        "Get device details",
        device_id=str(device_id),
        request_id=request_id,
    )

    # Get device
    result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise DeviceNotFoundError(device_id, request_id=request_id)

    # Get latest metrics (one per type)
    latest_metrics = []
    for metric_type in ["energy_consumed", "distance_traveled", "battery_level", "uptime"]:
        metric_result = await db.execute(
            select(DeviceMetric)
            .where(
                DeviceMetric.device_id == device_id,
                DeviceMetric.metric_type == metric_type,
            )
            .order_by(DeviceMetric.timestamp.desc())
            .limit(1)
        )
        metric = metric_result.scalar_one_or_none()
        if metric:
            latest_metrics.append(
                MetricSummary(
                    metric_type=metric.metric_type.value,
                    value=float(metric.value),
                    unit=metric.unit,
                    timestamp=metric.timestamp,
                )
            )

    # Get current activation
    activation_result = await db.execute(
        select(DeviceActivation)
        .where(
            DeviceActivation.device_id == device_id,
            DeviceActivation.activated_at.isnot(None),
        )
        .order_by(DeviceActivation.created_at.desc())
        .limit(1)
    )
    activation = activation_result.scalar_one_or_none()

    active_activation = None
    if activation and (not activation.expires_at or activation.expires_at > datetime.now(timezone.utc)):
        from app.schemas.device import ActivationSummary
        active_activation = ActivationSummary(
            id=activation.id,
            activation_type=activation.activation_type.value,
            expires_at=activation.expires_at,
            is_active=True,
        )

    return DeviceWithMetrics(
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
        latest_metrics=latest_metrics,
        active_activation=active_activation,
    )


@router.post(
    "/devices/{device_id}/activate",
    response_model=DeviceActivateResponse,
    responses={
        403: {"model": ErrorResponse, "description": "Device already active"},
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def activate_device(
    device_id: UUID,
    activation_request: DeviceActivateRequest,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Manually activate a device.

    Requires reason for audit trail.
    """
    logger.info(
        "Manual device activation",
        device_id=str(device_id),
        days_valid=activation_request.days_valid,
        reason=activation_request.reason,
        request_id=request_id,
    )

    service = OpenPAYGOService(db)

    # Generate token
    token, expires_at = await service.generate_token(
        device_id=device_id,
        days_valid=activation_request.days_valid,
    )

    # Activate device
    activation = await service.activate_device(
        device_id=device_id,
        token=token,
        activation_type=ActivationType.MANUAL,
        days_valid=activation_request.days_valid,
    )

    await db.commit()

    logger.info(
        "Device manually activated",
        device_id=str(device_id),
        activation_id=str(activation.id),
        request_id=request_id,
    )

    # Audit log
    audit_logger.log_event(
        event_type="device",
        action="manual_activate",
        resource_type="device",
        resource_id=str(device_id),
        details={
            "days_valid": activation_request.days_valid,
            "reason": activation_request.reason,
            "activation_id": str(activation.id),
        },
    )

    return DeviceActivateResponse(
        activation_id=activation.id,
        activation_token=token,
        expires_at=expires_at,
    )


@router.post(
    "/devices/{device_id}/suspend",
    response_model=DeviceResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def suspend_device(
    device_id: UUID,
    suspend_request: DeviceSuspendRequest,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Suspend a device.

    Requires reason for audit trail.
    """
    logger.info(
        "Device suspension",
        device_id=str(device_id),
        reason=suspend_request.reason,
        request_id=request_id,
    )

    result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise DeviceNotFoundError(device_id, request_id=request_id)

    # Suspend device
    device.transition_status(
        DeviceStatus.SUSPENDED,
        reason=suspend_request.reason,
    )

    await db.commit()
    await db.refresh(device)

    logger.info(
        "Device suspended",
        device_id=str(device_id),
        request_id=request_id,
    )

    # Audit log
    audit_logger.log_event(
        event_type="device",
        action="suspend",
        resource_type="device",
        resource_id=str(device_id),
        details={"reason": suspend_request.reason},
    )

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
# CUSTOMER MANAGEMENT
# =============================================================================


@router.get(
    "/customers",
    response_model=PaginatedResponse[CustomerResponse],
)
async def list_customers(
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
    pagination: PaginationDep,
    payment_provider: Optional[str] = Query(None, description="Filter by payment provider"),
):
    """
    List all customers with pagination.
    """
    logger.debug(
        "List customers",
        page=pagination.page,
        payment_provider=payment_provider,
        request_id=request_id,
    )

    query = select(Customer)

    if payment_provider:
        query = query.where(Customer.payment_provider == payment_provider)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset(pagination.offset).limit(pagination.page_size)
    query = query.order_by(Customer.created_at.desc())

    result = await db.execute(query)
    customers = result.scalars().all()

    items = [
        CustomerResponse(
            id=c.id,
            external_id=c.external_id,
            email=c.email,
            phone=c.phone,
            payment_provider=c.payment_provider,
            payment_provider_id=c.payment_provider_id,
            metadata=c.metadata_,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in customers
    ]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "/customers",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        409: {"model": ErrorResponse, "description": "Customer already exists"},
    },
)
async def create_customer(
    customer_data: CustomerCreate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Create a new customer.
    """
    logger.info(
        "Create customer request",
        external_id=customer_data.external_id,
        payment_provider=customer_data.payment_provider.value,
        request_id=request_id,
    )

    # Check for existing customer with same external_id
    existing = await db.execute(
        select(Customer).where(Customer.external_id == customer_data.external_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "CUSTOMER_EXISTS",
                "message": f"Customer with external_id '{customer_data.external_id}' already exists",
                "request_id": request_id,
            },
        )

    customer = Customer(
        external_id=customer_data.external_id,
        email=customer_data.email,
        phone=customer_data.phone,
        payment_provider=customer_data.payment_provider,
        payment_provider_id=customer_data.payment_provider_id,
        metadata_=customer_data.metadata or {},
    )

    db.add(customer)
    await db.commit()
    await db.refresh(customer)

    logger.info(
        "Customer created",
        customer_id=str(customer.id),
        external_id=customer.external_id,
        request_id=request_id,
    )

    audit_logger.log_event(
        event_type="customer",
        action="create",
        resource_type="customer",
        resource_id=str(customer.id),
        details={
            "external_id": customer.external_id,
            "payment_provider": customer.payment_provider.value,
        },
    )

    return CustomerResponse(
        id=customer.id,
        external_id=customer.external_id,
        email=customer.email,
        phone=customer.phone,
        payment_provider=customer.payment_provider,
        payment_provider_id=customer.payment_provider_id,
        metadata=customer.metadata_,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


@router.get(
    "/customers/{customer_id}",
    response_model=CustomerWithDevices,
    responses={
        404: {"model": ErrorResponse, "description": "Customer not found"},
    },
)
async def get_customer_details(
    customer_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Get customer details including associated devices.
    """
    result = await db.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "CUSTOMER_NOT_FOUND",
                "message": f"Customer not found: {customer_id}",
                "request_id": request_id,
            },
        )

    # Get associated devices
    devices_result = await db.execute(
        select(Device).where(Device.customer_id == customer_id)
    )
    devices = devices_result.scalars().all()

    return CustomerWithDevices(
        id=customer.id,
        external_id=customer.external_id,
        email=customer.email,
        phone=customer.phone,
        payment_provider=customer.payment_provider,
        payment_provider_id=customer.payment_provider_id,
        metadata=customer.metadata_,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        device_count=len(devices),
        devices=[
            CustomerDeviceSummary(
                id=d.id,
                external_id=d.external_id,
                device_type=d.device_type.value,
                status=d.status.value,
            )
            for d in devices
        ],
    )


@router.patch(
    "/customers/{customer_id}",
    response_model=CustomerResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Customer not found"},
    },
)
async def update_customer(
    customer_id: UUID,
    update_data: CustomerUpdate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Update a customer.
    """
    logger.info(
        "Update customer request",
        customer_id=str(customer_id),
        request_id=request_id,
    )

    result = await db.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "CUSTOMER_NOT_FOUND",
                "message": f"Customer not found: {customer_id}",
                "request_id": request_id,
            },
        )

    # Update provided fields
    update_fields = update_data.model_dump(exclude_unset=True)
    if "metadata" in update_fields:
        update_fields["metadata_"] = update_fields.pop("metadata")

    for field, value in update_fields.items():
        setattr(customer, field, value)

    await db.commit()
    await db.refresh(customer)

    logger.info(
        "Customer updated",
        customer_id=str(customer.id),
        updated_fields=list(update_fields.keys()),
        request_id=request_id,
    )

    return CustomerResponse(
        id=customer.id,
        external_id=customer.external_id,
        email=customer.email,
        phone=customer.phone,
        payment_provider=customer.payment_provider,
        payment_provider_id=customer.payment_provider_id,
        metadata=customer.metadata_,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


# =============================================================================
# TRANSACTIONS
# =============================================================================


@router.get(
    "/transactions",
    response_model=PaginatedResponse[TransactionResponse],
)
async def list_transactions(
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
    pagination: PaginationDep,
    status_filter: Optional[TransactionStatus] = Query(None, alias="status"),
    customer_id: Optional[UUID] = Query(None),
    device_id: Optional[UUID] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
):
    """
    List transactions with pagination and filters.
    """
    logger.debug(
        "List transactions",
        page=pagination.page,
        status_filter=status_filter.value if status_filter else None,
        customer_id=str(customer_id) if customer_id else None,
        request_id=request_id,
    )

    # Build query
    query = select(Transaction)

    if status_filter:
        query = query.where(Transaction.status == status_filter)

    if customer_id:
        query = query.where(Transaction.customer_id == customer_id)

    if device_id:
        query = query.where(Transaction.device_id == device_id)

    if date_from:
        query = query.where(Transaction.created_at >= date_from)

    if date_to:
        query = query.where(Transaction.created_at <= date_to)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated results
    query = query.offset(pagination.offset).limit(pagination.page_size)
    query = query.order_by(Transaction.created_at.desc())

    result = await db.execute(query)
    transactions = result.scalars().all()

    items = [
        TransactionResponse(
            id=t.id,
            customer_id=t.customer_id,
            device_id=t.device_id,
            payment_provider=t.payment_provider,
            provider_transaction_id=t.provider_transaction_id,
            amount=t.amount,
            currency=t.currency,
            status=t.status,
            idempotency_key=t.idempotency_key,
            failure_reason=t.failure_reason,
            metadata=t.metadata_,
            created_at=t.created_at,
            updated_at=t.updated_at,
            completed_at=t.completed_at,
        )
        for t in transactions
    ]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionWithDetails,
    responses={
        404: {"model": ErrorResponse, "description": "Transaction not found"},
    },
)
async def get_transaction_details(
    transaction_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Get transaction details including related entities.
    """
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    transaction = result.scalar_one_or_none()

    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TRANSACTION_NOT_FOUND",
                "message": f"Transaction not found: {transaction_id}",
                "request_id": request_id,
            },
        )

    # Get customer and device external IDs
    customer_external_id = None
    if transaction.customer_id:
        customer_result = await db.execute(
            select(Customer.external_id).where(Customer.id == transaction.customer_id)
        )
        customer_external_id = customer_result.scalar()

    device_external_id = None
    if transaction.device_id:
        device_result = await db.execute(
            select(Device.external_id).where(Device.id == transaction.device_id)
        )
        device_external_id = device_result.scalar()

    # Get related activations
    activations_result = await db.execute(
        select(DeviceActivation).where(DeviceActivation.transaction_id == transaction_id)
    )
    activations = activations_result.scalars().all()

    from app.schemas.transaction import TransactionActivationSummary

    return TransactionWithDetails(
        id=transaction.id,
        customer_id=transaction.customer_id,
        device_id=transaction.device_id,
        payment_provider=transaction.payment_provider,
        provider_transaction_id=transaction.provider_transaction_id,
        amount=transaction.amount,
        currency=transaction.currency,
        status=transaction.status,
        idempotency_key=transaction.idempotency_key,
        failure_reason=transaction.failure_reason,
        metadata=transaction.metadata_,
        created_at=transaction.created_at,
        updated_at=transaction.updated_at,
        completed_at=transaction.completed_at,
        customer_external_id=customer_external_id,
        device_external_id=device_external_id,
        activations=[
            TransactionActivationSummary(
                id=a.id,
                activation_type=a.activation_type.value,
                expires_at=a.expires_at,
                activated_at=a.activated_at,
            )
            for a in activations
        ],
    )


# =============================================================================
# ANALYTICS
# =============================================================================


@router.get(
    "/analytics/overview",
    response_model=AnalyticsOverview,
)
async def get_analytics_overview(
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Get analytics overview for dashboard.

    TODO: Implement Redis caching for performance.
    """
    logger.debug(
        "Get analytics overview",
        request_id=request_id,
    )

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    # Total devices
    total_devices_result = await db.execute(
        select(func.count()).select_from(Device)
    )
    total_devices = total_devices_result.scalar() or 0

    # Active devices
    active_devices_result = await db.execute(
        select(func.count())
        .select_from(Device)
        .where(Device.status == DeviceStatus.ACTIVE)
    )
    active_devices = active_devices_result.scalar() or 0

    # Total customers
    total_customers_result = await db.execute(
        select(func.count()).select_from(Customer)
    )
    total_customers = total_customers_result.scalar() or 0

    # Total revenue (completed transactions)
    revenue_result = await db.execute(
        select(func.sum(Transaction.amount))
        .where(Transaction.status == TransactionStatus.COMPLETED)
    )
    total_revenue = float(revenue_result.scalar() or 0)

    # Payment success rate
    total_transactions_result = await db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.status.in_([TransactionStatus.COMPLETED, TransactionStatus.FAILED]))
    )
    total_processed = total_transactions_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.status == TransactionStatus.COMPLETED)
    )
    completed_count = completed_result.scalar() or 0

    success_rate = (completed_count / total_processed * 100) if total_processed > 0 else 100

    # Today's transactions
    today_transactions_result = await db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.created_at >= today_start)
    )
    today_transactions = today_transactions_result.scalar() or 0

    # Yesterday's transactions
    yesterday_transactions_result = await db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.created_at >= yesterday_start,
            Transaction.created_at < today_start,
        )
    )
    yesterday_transactions = yesterday_transactions_result.scalar() or 0

    return AnalyticsOverview(
        total_devices=total_devices,
        active_devices=active_devices,
        total_customers=total_customers,
        total_revenue=total_revenue,
        payment_success_rate=round(success_rate, 2),
        today_transactions=today_transactions,
        yesterday_transactions=yesterday_transactions,
    )


@router.get(
    "/analytics/revenue",
    response_model=list[RevenueData],
)
async def get_revenue_analytics(
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
    period: str = Query("day", description="Aggregation period: day, week, month"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
):
    """
    Get revenue analytics by period.
    """
    logger.debug(
        "Get revenue analytics",
        period=period,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        request_id=request_id,
    )

    # Default date range: last 30 days
    if not date_to:
        date_to = datetime.now(timezone.utc)
    if not date_from:
        date_from = date_to - timedelta(days=30)

    # For simplicity, we'll return daily aggregates
    # In production, use proper date_trunc based on period

    result = await db.execute(
        select(
            func.date(Transaction.created_at).label("date"),
            func.sum(Transaction.amount).label("amount"),
            Transaction.currency,
            func.count().label("count"),
        )
        .where(
            Transaction.status == TransactionStatus.COMPLETED,
            Transaction.created_at >= date_from,
            Transaction.created_at <= date_to,
        )
        .group_by(func.date(Transaction.created_at), Transaction.currency)
        .order_by(func.date(Transaction.created_at))
    )

    rows = result.fetchall()

    return [
        RevenueData(
            period=str(row.date),
            amount=float(row.amount or 0),
            currency=row.currency or "USD",
            transaction_count=row.count,
        )
        for row in rows
    ]
