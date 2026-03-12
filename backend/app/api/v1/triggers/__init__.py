"""
Payment Trigger API Endpoints

Handles:
- Trigger configuration
- Manual trigger evaluation
- Trigger status

CRITICAL: All trigger operations are logged.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AdminAuthDep, DbSessionDep, RequestIdDep
from app.core.exceptions import DeviceNotFoundError
from app.core.logging import audit_logger, get_logger
from app.models.device import Device
from app.models.payment_trigger import PaymentTrigger, TriggerType
from app.schemas.common import ErrorResponse
from app.schemas.payment_trigger import (
    TriggerCreate,
    TriggerEvaluationRequest,
    TriggerEvaluationResult,
    TriggerResponse,
    TriggerUpdate,
)
from app.services.trigger_service import TriggerService

router = APIRouter()
logger = get_logger(__name__)


@router.post(
    "/configure",
    response_model=TriggerResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        404: {"model": ErrorResponse, "description": "Device not found"},
        409: {"model": ErrorResponse, "description": "Trigger already exists"},
    },
)
async def configure_trigger(
    trigger_data: TriggerCreate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Configure a payment trigger for a device.

    Creates a new trigger or updates if one of same type exists.
    """
    logger.info(
        "Configure trigger request",
        device_id=str(trigger_data.device_id),
        trigger_type=trigger_data.trigger_type.value,
        request_id=request_id,
    )

    # Verify device exists
    result = await db.execute(
        select(Device).where(Device.id == trigger_data.device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise DeviceNotFoundError(trigger_data.device_id, request_id=request_id)

    # Check for existing trigger of same type
    existing = await db.execute(
        select(PaymentTrigger).where(
            PaymentTrigger.device_id == trigger_data.device_id,
            PaymentTrigger.trigger_type == trigger_data.trigger_type,
        )
    )
    existing_trigger = existing.scalar_one_or_none()

    if existing_trigger:
        # Update existing trigger
        existing_trigger.config = trigger_data.config
        existing_trigger.is_active = trigger_data.is_active

        # Validate config
        errors = existing_trigger.validate_config_for_type()
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_CONFIG",
                    "message": "Trigger configuration is invalid",
                    "errors": errors,
                    "request_id": request_id,
                },
            )

        await db.commit()
        await db.refresh(existing_trigger)

        logger.info(
            "Trigger updated",
            trigger_id=str(existing_trigger.id),
            device_id=str(trigger_data.device_id),
            request_id=request_id,
        )

        trigger = existing_trigger
    else:
        # Create new trigger
        trigger = PaymentTrigger(
            device_id=trigger_data.device_id,
            trigger_type=trigger_data.trigger_type,
            config=trigger_data.config,
            is_active=trigger_data.is_active,
        )

        # Validate config
        errors = trigger.validate_config_for_type()
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_CONFIG",
                    "message": "Trigger configuration is invalid",
                    "errors": errors,
                    "request_id": request_id,
                },
            )

        db.add(trigger)
        await db.commit()
        await db.refresh(trigger)

        logger.info(
            "Trigger created",
            trigger_id=str(trigger.id),
            device_id=str(trigger_data.device_id),
            request_id=request_id,
        )

    # Audit log
    audit_logger.log_event(
        event_type="trigger",
        action="configure",
        resource_type="payment_trigger",
        resource_id=str(trigger.id),
        details={
            "device_id": str(trigger_data.device_id),
            "trigger_type": trigger_data.trigger_type.value,
            "is_active": trigger_data.is_active,
        },
    )

    return TriggerResponse(
        id=trigger.id,
        device_id=trigger.device_id,
        trigger_type=trigger.trigger_type,
        config=trigger.config,
        is_active=trigger.is_active,
        last_triggered_at=trigger.last_triggered_at,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
    )


@router.post(
    "/evaluate",
    response_model=list[TriggerEvaluationResult],
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def evaluate_triggers(
    evaluation_request: TriggerEvaluationRequest,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Manually evaluate triggers for a device.

    Admin only. Returns evaluation results.
    """
    logger.info(
        "Manual trigger evaluation request",
        device_id=str(evaluation_request.device_id),
        trigger_types=[t.value for t in evaluation_request.trigger_types]
        if evaluation_request.trigger_types
        else "all",
        dry_run=evaluation_request.dry_run,
        request_id=request_id,
    )

    service = TriggerService(db)

    try:
        results = await service.evaluate_triggers_for_device(
            device_id=evaluation_request.device_id,
            trigger_types=evaluation_request.trigger_types,
            dry_run=evaluation_request.dry_run,
        )

        await db.commit()

        logger.info(
            "Trigger evaluation completed",
            device_id=str(evaluation_request.device_id),
            total_triggers=len(results),
            triggered_count=sum(1 for r in results if r.triggered),
            request_id=request_id,
        )

        return [
            TriggerEvaluationResult(
                device_id=r.device_id,
                trigger_id=r.trigger_id,
                trigger_type=r.trigger_type,
                triggered=r.triggered,
                reason=r.reason,
                current_value=r.current_value,
                threshold=r.threshold,
                action_taken=r.action_taken,
                payment_created=r.payment_created,
                transaction_id=r.transaction_id,
            )
            for r in results
        ]

    except DeviceNotFoundError:
        raise


@router.get(
    "/{device_id}",
    response_model=list[TriggerResponse],
    responses={
        404: {"model": ErrorResponse, "description": "Device not found"},
    },
)
async def get_device_triggers(
    device_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
    active_only: bool = Query(False, description="Only return active triggers"),
):
    """
    Get all triggers for a device.
    """
    logger.debug(
        "Get device triggers",
        device_id=str(device_id),
        active_only=active_only,
        request_id=request_id,
    )

    # Verify device exists
    result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise DeviceNotFoundError(device_id, request_id=request_id)

    # Get triggers
    query = select(PaymentTrigger).where(PaymentTrigger.device_id == device_id)

    if active_only:
        query = query.where(PaymentTrigger.is_active == True)

    result = await db.execute(query)
    triggers = result.scalars().all()

    return [
        TriggerResponse(
            id=t.id,
            device_id=t.device_id,
            trigger_type=t.trigger_type,
            config=t.config,
            is_active=t.is_active,
            last_triggered_at=t.last_triggered_at,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in triggers
    ]


@router.patch(
    "/{trigger_id}",
    response_model=TriggerResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Trigger not found"},
    },
)
async def update_trigger(
    trigger_id: UUID,
    update_data: TriggerUpdate,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Update a trigger configuration.
    """
    logger.info(
        "Update trigger request",
        trigger_id=str(trigger_id),
        request_id=request_id,
    )

    result = await db.execute(
        select(PaymentTrigger).where(PaymentTrigger.id == trigger_id)
    )
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TRIGGER_NOT_FOUND",
                "message": f"Trigger not found: {trigger_id}",
                "request_id": request_id,
            },
        )

    # Update fields
    if update_data.config is not None:
        trigger.config = update_data.config
        errors = trigger.validate_config_for_type()
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "INVALID_CONFIG",
                    "message": "Trigger configuration is invalid",
                    "errors": errors,
                    "request_id": request_id,
                },
            )

    if update_data.is_active is not None:
        if update_data.is_active:
            trigger.activate()
        else:
            trigger.deactivate()

    if update_data.last_triggered_at is not None:
        trigger.last_triggered_at = update_data.last_triggered_at

    await db.commit()
    await db.refresh(trigger)

    logger.info(
        "Trigger updated",
        trigger_id=str(trigger.id),
        request_id=request_id,
    )

    return TriggerResponse(
        id=trigger.id,
        device_id=trigger.device_id,
        trigger_type=trigger.trigger_type,
        config=trigger.config,
        is_active=trigger.is_active,
        last_triggered_at=trigger.last_triggered_at,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
    )


@router.delete(
    "/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse, "description": "Trigger not found"},
    },
)
async def delete_trigger(
    trigger_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin_key: AdminAuthDep,
):
    """
    Delete a trigger.
    """
    logger.info(
        "Delete trigger request",
        trigger_id=str(trigger_id),
        request_id=request_id,
    )

    result = await db.execute(
        select(PaymentTrigger).where(PaymentTrigger.id == trigger_id)
    )
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TRIGGER_NOT_FOUND",
                "message": f"Trigger not found: {trigger_id}",
                "request_id": request_id,
            },
        )

    await db.delete(trigger)
    await db.commit()

    logger.info(
        "Trigger deleted",
        trigger_id=str(trigger_id),
        device_id=str(trigger.device_id),
        request_id=request_id,
    )

    # Audit log
    audit_logger.log_event(
        event_type="trigger",
        action="delete",
        resource_type="payment_trigger",
        resource_id=str(trigger_id),
        details={
            "device_id": str(trigger.device_id),
            "trigger_type": trigger.trigger_type.value,
        },
    )
