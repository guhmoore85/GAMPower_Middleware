"""
Authentication API Endpoints

Provides:
- POST /login - Authenticate and get tokens
- POST /logout - Revoke refresh token
- POST /logout-all - Revoke all sessions
- POST /refresh - Refresh access token
- GET /me - Get current user info
- POST /change-password - Change password

CRITICAL: All authentication events are logged LOUDLY.
Rate limiting is enforced on login endpoint.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field

from app.core.dependencies import (
    CurrentActiveUserDep,
    DbSessionDep,
    RequestIdDep,
)
from app.core.exceptions import AuthenticationError, RateLimitError
from app.core.logging import audit_logger, get_logger
from app.services.auth_service import AuthService

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# Request/Response Schemas
# =============================================================================


class LoginRequest(BaseModel):
    """Login request body."""

    username: str = Field(..., min_length=1, description="Username or email")
    password: str = Field(..., min_length=1, description="Password")

    class Config:
        json_schema_extra = {
            "example": {
                "username": "admin",
                "password": "SecurePassword123",
            }
        }


class TokenResponse(BaseModel):
    """Authentication token response."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="Refresh token for getting new access tokens")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(..., description="Access token expiry in seconds")

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "dG9rZW5fc2VjcmV0X2hlcmU...",
                "token_type": "bearer",
                "expires_in": 1800,
            }
        }


class LoginResponse(TokenResponse):
    """Login response with user info."""

    user: dict = Field(..., description="User information")

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "dG9rZW5fc2VjcmV0X2hlcmU...",
                "token_type": "bearer",
                "expires_in": 1800,
                "user": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "username": "admin",
                    "email": "admin@example.com",
                    "full_name": "Admin User",
                    "role": "admin",
                    "is_active": True,
                },
            }
        }


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str = Field(..., min_length=1, description="Refresh token")


class RefreshResponse(BaseModel):
    """Token refresh response."""

    access_token: str = Field(..., description="New JWT access token")
    refresh_token: Optional[str] = Field(None, description="New refresh token (if rotated)")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")


class LogoutRequest(BaseModel):
    """Logout request."""

    refresh_token: str = Field(..., min_length=1, description="Refresh token to revoke")


class ChangePasswordRequest(BaseModel):
    """Password change request."""

    current_password: str = Field(..., min_length=1, description="Current password")
    new_password: str = Field(..., min_length=8, description="New password (min 8 characters)")

    class Config:
        json_schema_extra = {
            "example": {
                "current_password": "OldPassword123",
                "new_password": "NewSecurePassword456",
            }
        }


class UserResponse(BaseModel):
    """Current user response."""

    id: str
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


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
    success: bool = True


# =============================================================================
# Authentication Endpoints
# =============================================================================


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"description": "Invalid credentials"},
        429: {"description": "Too many failed attempts"},
    },
)
async def login(
    request: Request,
    request_id: RequestIdDep,
    db: DbSessionDep,
    login_data: LoginRequest,
    user_agent: Optional[str] = Header(None),
):
    """
    Authenticate user and return JWT tokens.

    - Validates username/email and password
    - Returns access token (30 min) and refresh token (7 days)
    - Rate limited: 5 failed attempts per 15 minutes

    LOUD: Logs all login attempts with outcome.
    """
    logger.info(
        "Login endpoint called",
        username=login_data.username,
        request_id=request_id,
    )

    # Get client IP
    ip_address = request.client.host if request.client else None

    auth_service = AuthService(db)

    try:
        result = await auth_service.login(
            username=login_data.username,
            password=login_data.password,
            device_info=user_agent,
            ip_address=ip_address,
        )

        logger.info(
            "Login successful",
            user_id=str(result.user.id),
            username=result.user.username,
            request_id=request_id,
        )

        return LoginResponse(**result.to_dict())

    except RateLimitError as e:
        logger.warning(
            "Login rate limited",
            username=login_data.username,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": "RATE_LIMIT_ERROR",
                "message": str(e),
                "request_id": request_id,
            },
            headers={"Retry-After": str(e.context.get("retry_after_seconds", 900))},
        )

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": str(e),
                "request_id": request_id,
            },
        )


@router.post(
    "/login/form",
    response_model=LoginResponse,
    include_in_schema=False,  # OAuth2 form endpoint
)
async def login_form(
    request: Request,
    request_id: RequestIdDep,
    db: DbSessionDep,
    form_data: OAuth2PasswordRequestForm = Depends(),
    user_agent: Optional[str] = Header(None),
):
    """
    OAuth2 password flow login endpoint.

    Same as /login but accepts form data for OAuth2 compatibility.
    """
    login_data = LoginRequest(username=form_data.username, password=form_data.password)

    return await login(
        request=request,
        request_id=request_id,
        db=db,
        login_data=login_data,
        user_agent=user_agent,
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
)
async def logout(
    request_id: RequestIdDep,
    db: DbSessionDep,
    logout_data: LogoutRequest,
    current_user: CurrentActiveUserDep,
):
    """
    Logout by revoking refresh token.

    Requires valid access token to identify the user.

    LOUD: Logs logout events.
    """
    logger.info(
        "Logout requested",
        user_id=str(current_user.id),
        request_id=request_id,
    )

    auth_service = AuthService(db)
    success = await auth_service.logout(
        refresh_token=logout_data.refresh_token,
        user_id=current_user.id,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_TOKEN",
                "message": "Invalid or already revoked refresh token",
                "request_id": request_id,
            },
        )

    return MessageResponse(message="Logged out successfully")


@router.post(
    "/logout-all",
    response_model=MessageResponse,
)
async def logout_all(
    request_id: RequestIdDep,
    db: DbSessionDep,
    current_user: CurrentActiveUserDep,
):
    """
    Logout from all sessions.

    Revokes all refresh tokens for the current user.

    LOUD: Logs mass logout events.
    """
    logger.info(
        "Logout all sessions requested",
        user_id=str(current_user.id),
        request_id=request_id,
    )

    auth_service = AuthService(db)
    count = await auth_service.logout_all(user_id=current_user.id)

    return MessageResponse(
        message=f"Logged out from {count} session(s)",
        success=True,
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    responses={
        401: {"description": "Invalid or expired refresh token"},
    },
)
async def refresh_token(
    request_id: RequestIdDep,
    db: DbSessionDep,
    refresh_data: RefreshRequest,
):
    """
    Refresh access token using refresh token.

    - Validates refresh token
    - Issues new access token
    - Optionally rotates refresh token (recommended)

    Does NOT require current access token.
    """
    logger.debug(
        "Token refresh requested",
        request_id=request_id,
    )

    auth_service = AuthService(db)

    try:
        result = await auth_service.refresh_tokens(
            refresh_token=refresh_data.refresh_token,
            rotate_refresh_token=True,  # Always rotate for security
        )

        return RefreshResponse(**result.to_dict())

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": str(e),
                "request_id": request_id,
            },
        )


@router.get(
    "/me",
    response_model=UserResponse,
)
async def get_current_user(
    request_id: RequestIdDep,
    current_user: CurrentActiveUserDep,
):
    """
    Get current authenticated user information.

    Returns user profile without sensitive data.
    """
    logger.debug(
        "Get current user",
        user_id=str(current_user.id),
        request_id=request_id,
    )

    return UserResponse(
        id=str(current_user.id),
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role.value,
        is_active=current_user.is_active,
        last_login_at=current_user.last_login_at,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@router.post(
    "/change-password",
    response_model=MessageResponse,
    responses={
        400: {"description": "Password validation failed"},
        401: {"description": "Current password is incorrect"},
    },
)
async def change_password(
    request_id: RequestIdDep,
    db: DbSessionDep,
    password_data: ChangePasswordRequest,
    current_user: CurrentActiveUserDep,
):
    """
    Change current user's password.

    Requirements:
    - Current password must be correct
    - New password must be at least 8 characters
    - New password must contain uppercase, lowercase, and digit

    All other sessions will be logged out.

    LOUD: Logs password change events.
    """
    logger.info(
        "Password change requested",
        user_id=str(current_user.id),
        request_id=request_id,
    )

    auth_service = AuthService(db)

    try:
        await auth_service.change_password(
            user_id=current_user.id,
            current_password=password_data.current_password,
            new_password=password_data.new_password,
            revoke_sessions=True,
        )

        return MessageResponse(
            message="Password changed successfully. Please log in again.",
            success=True,
        )

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
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


# =============================================================================
# Session Management (Optional)
# =============================================================================


@router.get(
    "/sessions",
    response_model=list[dict],
)
async def list_sessions(
    request_id: RequestIdDep,
    db: DbSessionDep,
    current_user: CurrentActiveUserDep,
):
    """
    List all active sessions for current user.

    Returns refresh tokens (without the actual token values).
    """
    from sqlalchemy import select
    from app.models.refresh_token import RefreshToken

    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.is_revoked == False,  # noqa: E712
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        .order_by(RefreshToken.created_at.desc())
    )
    tokens = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "device_info": t.device_info,
            "ip_address": t.ip_address,
            "created_at": t.created_at.isoformat(),
            "expires_at": t.expires_at.isoformat(),
        }
        for t in tokens
    ]


@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
)
async def revoke_session(
    session_id: str,
    request_id: RequestIdDep,
    db: DbSessionDep,
    current_user: CurrentActiveUserDep,
):
    """
    Revoke a specific session.

    Can only revoke own sessions.
    """
    from uuid import UUID
    from sqlalchemy import select
    from app.models.refresh_token import RefreshToken

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "VALIDATION_ERROR",
                "message": "Invalid session ID format",
                "request_id": request_id,
            },
        )

    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.id == session_uuid,
            RefreshToken.user_id == current_user.id,
        )
    )
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "message": "Session not found",
                "request_id": request_id,
            },
        )

    token.revoke(reason="user_revoked_session")
    await db.commit()

    logger.info(
        "Session revoked",
        session_id=session_id,
        user_id=str(current_user.id),
        request_id=request_id,
    )

    return MessageResponse(message="Session revoked successfully")
