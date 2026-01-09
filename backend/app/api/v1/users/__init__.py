"""
User Management API Endpoints (Admin Only)

Provides:
- POST /users - Create user
- GET /users - List users
- GET /users/{user_id} - Get user details
- PATCH /users/{user_id} - Update user
- DELETE /users/{user_id} - Deactivate user
- POST /users/{user_id}/reset-password - Admin password reset

CRITICAL: All endpoints require admin role.
All user operations are logged for audit.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.core.dependencies import (
    DbSessionDep,
    PaginationDep,
    RequestIdDep,
    require_admin,
)
from app.core.logging import audit_logger, get_logger
from app.core.security import hash_password
from app.models.refresh_token import revoke_all_user_tokens
from app.models.user import User, UserRole
from app.schemas.common import ErrorResponse, PaginatedResponse
from app.services.auth_service import AuthService

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# Request/Response Schemas
# =============================================================================


class UserCreateRequest(BaseModel):
    """User creation request."""

    username: str = Field(
        ..., min_length=3, max_length=50,
        description="Username (3-50 chars, alphanumeric with _ and -)"
    )
    email: EmailStr = Field(..., description="Email address")
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")
    full_name: Optional[str] = Field(None, max_length=100, description="Display name")
    role: str = Field(default="readonly", description="User role")

    class Config:
        json_schema_extra = {
            "example": {
                "username": "newuser",
                "email": "newuser@example.com",
                "password": "SecurePassword123",
                "full_name": "New User",
                "role": "technician",
            }
        }


class UserUpdateRequest(BaseModel):
    """User update request."""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=100)
    role: Optional[str] = None
    is_active: Optional[bool] = None

    class Config:
        json_schema_extra = {
            "example": {
                "full_name": "Updated Name",
                "role": "support",
            }
        }


class AdminPasswordResetRequest(BaseModel):
    """Admin password reset request."""

    new_password: str = Field(..., min_length=8, description="New password")


class UserResponse(BaseModel):
    """User response."""

    id: UUID
    username: str
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserDetailResponse(UserResponse):
    """Detailed user response with additional info."""

    active_sessions: int = 0


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
    success: bool = True


# =============================================================================
# User Management Endpoints
# =============================================================================


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        409: {"model": ErrorResponse, "description": "Username or email already exists"},
    },
)
async def create_user(
    request_id: RequestIdDep,
    db: DbSessionDep,
    user_data: UserCreateRequest,
    admin: User = Depends(require_admin),
):
    """
    Create a new user (Admin only).

    LOUD: Logs user creation with admin context.
    """
    logger.info(
        "Creating user",
        username=user_data.username,
        role=user_data.role,
        admin_id=str(admin.id),
        request_id=request_id,
    )

    # Validate role
    try:
        role = UserRole(user_data.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "VALIDATION_ERROR",
                "message": f"Invalid role: {user_data.role}. Valid roles: {[r.value for r in UserRole]}",
                "request_id": request_id,
            },
        )

    # Check username uniqueness
    existing = await db.execute(
        select(User).where(User.username == user_data.username.lower())
    )
    if existing.scalar_one_or_none():
        logger.warning(
            "Username already exists",
            username=user_data.username,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "CONFLICT",
                "message": f"Username already exists: {user_data.username}",
                "request_id": request_id,
            },
        )

    # Check email uniqueness
    existing = await db.execute(
        select(User).where(User.email == user_data.email.lower())
    )
    if existing.scalar_one_or_none():
        logger.warning(
            "Email already exists",
            email=user_data.email,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "CONFLICT",
                "message": f"Email already exists: {user_data.email}",
                "request_id": request_id,
            },
        )

    # Validate password
    auth_service = AuthService(db)
    try:
        auth_service._validate_password(user_data.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "VALIDATION_ERROR",
                "message": str(e),
                "request_id": request_id,
            },
        )

    # Create user
    user = User(
        username=user_data.username.lower(),
        email=user_data.email.lower(),
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        role=role,
        is_active=True,
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(
        "User created",
        user_id=str(user.id),
        username=user.username,
        role=user.role.value,
        admin_id=str(admin.id),
        request_id=request_id,
    )

    audit_logger.log_event(
        event_type="user",
        action="create",
        resource_type="user",
        resource_id=str(user.id),
        details={
            "username": user.username,
            "role": user.role.value,
            "admin_id": str(admin.id),
        },
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get(
    "",
    response_model=PaginatedResponse[UserResponse],
)
async def list_users(
    request_id: RequestIdDep,
    db: DbSessionDep,
    pagination: PaginationDep,
    admin: User = Depends(require_admin),
    role_filter: Optional[str] = Query(None, alias="role"),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search username or email"),
):
    """
    List all users with pagination (Admin only).
    """
    logger.debug(
        "Listing users",
        page=pagination.page,
        role_filter=role_filter,
        is_active=is_active,
        request_id=request_id,
    )

    # Build query
    query = select(User)

    if role_filter:
        try:
            role = UserRole(role_filter)
            query = query.where(User.role == role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "VALIDATION_ERROR",
                    "message": f"Invalid role: {role_filter}",
                    "request_id": request_id,
                },
            )

    if is_active is not None:
        query = query.where(User.is_active == is_active)

    if search:
        search_term = f"%{search.lower()}%"
        query = query.where(
            (User.username.ilike(search_term)) | (User.email.ilike(search_term))
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated results
    query = query.offset(pagination.offset).limit(pagination.page_size)
    query = query.order_by(User.created_at.desc())

    result = await db.execute(query)
    users = result.scalars().all()

    items = [
        UserResponse(
            id=u.id,
            username=u.username,
            email=u.email,
            full_name=u.full_name,
            role=u.role.value,
            is_active=u.is_active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            updated_at=u.updated_at,
        )
        for u in users
    ]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{user_id}",
    response_model=UserDetailResponse,
    responses={
        404: {"model": ErrorResponse, "description": "User not found"},
    },
)
async def get_user(
    user_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin: User = Depends(require_admin),
):
    """
    Get user details by ID (Admin only).
    """
    logger.debug(
        "Getting user",
        user_id=str(user_id),
        request_id=request_id,
    )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "message": f"User not found: {user_id}",
                "request_id": request_id,
            },
        )

    # Count active sessions
    from app.models.refresh_token import RefreshToken
    from datetime import timezone

    sessions_result = await db.execute(
        select(func.count())
        .select_from(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked == False,  # noqa: E712
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    active_sessions = sessions_result.scalar() or 0

    return UserDetailResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        active_sessions=active_sessions,
    )


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        404: {"model": ErrorResponse, "description": "User not found"},
        409: {"model": ErrorResponse, "description": "Email already exists"},
    },
)
async def update_user(
    user_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    user_data: UserUpdateRequest,
    admin: User = Depends(require_admin),
):
    """
    Update user details (Admin only).

    Cannot change username.
    Deactivating a user revokes all their sessions.
    """
    logger.info(
        "Updating user",
        user_id=str(user_id),
        admin_id=str(admin.id),
        request_id=request_id,
    )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "message": f"User not found: {user_id}",
                "request_id": request_id,
            },
        )

    changes = {}

    # Update email
    if user_data.email is not None and user_data.email.lower() != user.email:
        # Check uniqueness
        existing = await db.execute(
            select(User).where(
                User.email == user_data.email.lower(),
                User.id != user_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_code": "CONFLICT",
                    "message": f"Email already exists: {user_data.email}",
                    "request_id": request_id,
                },
            )
        changes["email"] = {"old": user.email, "new": user_data.email.lower()}
        user.email = user_data.email.lower()

    # Update full name
    if user_data.full_name is not None:
        changes["full_name"] = {"old": user.full_name, "new": user_data.full_name}
        user.full_name = user_data.full_name

    # Update role
    if user_data.role is not None:
        try:
            new_role = UserRole(user_data.role)
            if new_role != user.role:
                changes["role"] = {"old": user.role.value, "new": new_role.value}
                user.role = new_role
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "VALIDATION_ERROR",
                    "message": f"Invalid role: {user_data.role}",
                    "request_id": request_id,
                },
            )

    # Update active status
    if user_data.is_active is not None and user_data.is_active != user.is_active:
        changes["is_active"] = {"old": user.is_active, "new": user_data.is_active}
        user.is_active = user_data.is_active

        # If deactivating, revoke all sessions
        if not user_data.is_active:
            await revoke_all_user_tokens(db, user_id, reason="user_deactivated")
            logger.warning(
                "User deactivated - all sessions revoked",
                user_id=str(user_id),
                admin_id=str(admin.id),
            )

    await db.commit()
    await db.refresh(user)

    if changes:
        logger.info(
            "User updated",
            user_id=str(user_id),
            changes=changes,
            admin_id=str(admin.id),
            request_id=request_id,
        )

        audit_logger.log_event(
            event_type="user",
            action="update",
            resource_type="user",
            resource_id=str(user_id),
            details={
                "changes": changes,
                "admin_id": str(admin.id),
            },
        )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.delete(
    "/{user_id}",
    response_model=MessageResponse,
    responses={
        404: {"model": ErrorResponse, "description": "User not found"},
    },
)
async def deactivate_user(
    user_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin: User = Depends(require_admin),
):
    """
    Deactivate a user (Admin only).

    Does not delete the user, only sets is_active=False.
    Revokes all sessions.

    LOUD: Logs deactivation with admin context.
    """
    logger.info(
        "Deactivating user",
        user_id=str(user_id),
        admin_id=str(admin.id),
        request_id=request_id,
    )

    # Prevent self-deactivation
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "VALIDATION_ERROR",
                "message": "Cannot deactivate yourself",
                "request_id": request_id,
            },
        )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "message": f"User not found: {user_id}",
                "request_id": request_id,
            },
        )

    if not user.is_active:
        return MessageResponse(message="User is already deactivated")

    user.deactivate(reason=f"Deactivated by admin {admin.username}")
    await revoke_all_user_tokens(db, user_id, reason="user_deactivated")

    await db.commit()

    logger.warning(
        "User deactivated",
        user_id=str(user_id),
        username=user.username,
        admin_id=str(admin.id),
        request_id=request_id,
    )

    return MessageResponse(message=f"User {user.username} deactivated successfully")


@router.post(
    "/{user_id}/reset-password",
    response_model=MessageResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Password validation failed"},
        404: {"model": ErrorResponse, "description": "User not found"},
    },
)
async def admin_reset_password(
    user_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    password_data: AdminPasswordResetRequest,
    admin: User = Depends(require_admin),
):
    """
    Reset user password (Admin only).

    Sets new password and revokes all sessions.

    LOUD: Logs admin password reset with audit trail.
    """
    logger.warning(
        "Admin password reset requested",
        user_id=str(user_id),
        admin_id=str(admin.id),
        request_id=request_id,
    )

    auth_service = AuthService(db)

    try:
        await auth_service.reset_password_admin(
            user_id=user_id,
            new_password=password_data.new_password,
            admin_id=admin.id,
        )

        return MessageResponse(
            message="Password reset successfully. User will need to log in again."
        )

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "message": str(e),
                "request_id": request_id,
            },
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "VALIDATION_ERROR",
                "message": str(e),
                "request_id": request_id,
            },
        )


@router.post(
    "/{user_id}/activate",
    response_model=UserResponse,
    responses={
        404: {"model": ErrorResponse, "description": "User not found"},
    },
)
async def activate_user(
    user_id: UUID,
    request_id: RequestIdDep,
    db: DbSessionDep,
    admin: User = Depends(require_admin),
):
    """
    Activate a deactivated user (Admin only).
    """
    logger.info(
        "Activating user",
        user_id=str(user_id),
        admin_id=str(admin.id),
        request_id=request_id,
    )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "message": f"User not found: {user_id}",
                "request_id": request_id,
            },
        )

    user.activate()
    await db.commit()
    await db.refresh(user)

    logger.info(
        "User activated",
        user_id=str(user_id),
        username=user.username,
        admin_id=str(admin.id),
        request_id=request_id,
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
