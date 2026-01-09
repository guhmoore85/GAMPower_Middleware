"""
FastAPI Dependencies for PAYGO Middleware

This module provides dependency injection for:
- Database sessions
- Authentication/Authorization (JWT and API Key)
- Role-based access control
- Request ID tracking
- Redis connections
- Common request parameters

CRITICAL: All dependencies validate their requirements loudly.
"""

from functools import wraps
from typing import Annotated, Callable, Optional
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import APIKeyHeader, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
)
from app.core.logging import audit_logger, get_logger, set_request_id
from app.core.security import decode_access_token, verify_admin_api_key
from app.db.session import get_db_session

logger = get_logger(__name__)

# Security schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


# =============================================================================
# Request ID Middleware Dependency
# =============================================================================


async def get_request_id(
    request: Request,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
) -> str:
    """
    Get or generate request ID for tracing.

    If X-Request-ID header is provided, use it.
    Otherwise, generate a new UUID.

    Sets request ID in logging context for correlation.
    """
    request_id = x_request_id or str(uuid4())

    # Set in logging context
    set_request_id(request_id)

    # Store in request state for later use
    request.state.request_id = request_id

    logger.debug(
        "Request ID assigned",
        request_id=request_id,
        source="header" if x_request_id else "generated",
    )

    return request_id


RequestIdDep = Annotated[str, Depends(get_request_id)]


# =============================================================================
# Database Session Dependency
# =============================================================================


async def get_db(request: Request) -> AsyncSession:
    """
    Get database session for request.

    LOUD: Logs session creation and handles connection errors.

    Yields:
        AsyncSession bound to request lifecycle
    """
    async for session in get_db_session():
        # Store in request state
        request.state.db = session

        logger.debug("Database session created")

        try:
            yield session
        finally:
            logger.debug("Database session closed")


DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


# =============================================================================
# API Key Authentication (Legacy/Webhooks)
# =============================================================================


async def verify_api_key_auth(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
) -> str:
    """
    Verify API key from X-API-Key header.

    LOUD: Logs all authentication attempts.

    Args:
        request: FastAPI request
        api_key: API key from header

    Returns:
        Validated API key

    Raises:
        HTTPException 401 if authentication fails
    """
    request_id = getattr(request.state, "request_id", None)

    if not api_key:
        logger.warning(
            "API key missing",
            endpoint=str(request.url.path),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "API key required. Provide X-API-Key header.",
                "request_id": request_id,
            },
        )

    # For now, we just validate format. In production, validate against DB.
    if len(api_key) < 32:
        logger.warning(
            "Invalid API key format",
            endpoint=str(request.url.path),
            key_length=len(api_key),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "Invalid API key format",
                "request_id": request_id,
            },
        )

    logger.debug("API key authenticated", endpoint=str(request.url.path))
    return api_key


ApiKeyDep = Annotated[str, Depends(verify_api_key_auth)]


# =============================================================================
# Admin API Key Authentication (Legacy - for backwards compatibility)
# =============================================================================


async def require_admin_auth(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
) -> str:
    """
    Require admin API key authentication.

    LOUD: Logs all admin access attempts.

    Args:
        request: FastAPI request
        api_key: API key from header

    Returns:
        Validated admin API key

    Raises:
        HTTPException 401/403 if authentication fails
    """
    request_id = getattr(request.state, "request_id", None)

    if not api_key:
        logger.warning(
            "Admin access attempted without API key",
            endpoint=str(request.url.path),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "Admin API key required",
                "request_id": request_id,
            },
        )

    if not verify_admin_api_key(api_key):
        logger.warning(
            "Admin access denied - invalid key",
            endpoint=str(request.url.path),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "AUTHORIZATION_ERROR",
                "message": "Invalid admin API key",
                "request_id": request_id,
            },
        )

    logger.info("Admin access granted", endpoint=str(request.url.path))
    return api_key


AdminAuthDep = Annotated[str, Depends(require_admin_auth)]


# =============================================================================
# JWT Token Authentication
# =============================================================================


async def get_current_user_claims(
    request: Request,
    credentials: Optional[str] = Depends(bearer_scheme),
) -> dict:
    """
    Get current user claims from JWT token.

    LOUD: Logs all token validation attempts.

    Returns:
        Token claims including user ID and role

    Raises:
        HTTPException 401 if token is invalid
    """
    request_id = getattr(request.state, "request_id", None)

    if not credentials:
        logger.warning(
            "JWT token missing",
            endpoint=str(request.url.path),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "Bearer token required",
                "request_id": request_id,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = decode_access_token(credentials.credentials)
        logger.debug("User authenticated", user_id=claims.get("sub"))

        # Store user info in request state
        request.state.user_id = claims.get("sub")
        request.state.user_role = claims.get("role")

        return claims

    except AuthenticationError as e:
        logger.warning(
            "JWT validation failed",
            endpoint=str(request.url.path),
            error=str(e),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=e.to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        )


# Alias for backwards compatibility
get_current_user = get_current_user_claims

CurrentUserDep = Annotated[dict, Depends(get_current_user_claims)]


# =============================================================================
# JWT + Database User Authentication
# =============================================================================


async def get_current_active_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_user_claims),
):
    """
    Get current authenticated user from database.

    Validates JWT token and fetches user from database.

    LOUD: Logs authentication and any failures.

    Returns:
        User model instance

    Raises:
        HTTPException 401 if user not found or inactive
    """
    from app.models.user import User

    request_id = getattr(request.state, "request_id", None)
    user_id = claims.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "Invalid token: missing subject",
                "request_id": request_id,
            },
        )

    try:
        result = await db.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = result.scalar_one_or_none()
    except Exception as e:
        logger.error(
            "Database error fetching user",
            user_id=user_id,
            error=str(e),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": "Failed to fetch user",
                "request_id": request_id,
            },
        )

    if not user:
        logger.warning(
            "User not found for valid token",
            user_id=user_id,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "User not found",
                "request_id": request_id,
            },
        )

    if not user.is_active:
        logger.warning(
            "Inactive user attempted access",
            user_id=user_id,
            username=user.username,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "User account is deactivated",
                "request_id": request_id,
            },
        )

    # Store user in request state for easy access
    request.state.current_user = user

    logger.debug(
        "User authenticated from database",
        user_id=str(user.id),
        username=user.username,
        role=user.role.value,
    )

    return user


# Import User type for annotation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User as UserModel

CurrentActiveUserDep = Annotated["UserModel", Depends(get_current_active_user)]


# =============================================================================
# Role-Based Access Control Dependencies
# =============================================================================


def require_role(*allowed_roles: str):
    """
    Create a dependency that requires specific roles.

    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(user: UserModel = Depends(require_role("admin"))):
            ...

    Args:
        allowed_roles: Role names that are allowed access

    Returns:
        Dependency function that validates role
    """
    from app.models.user import UserRole

    # Convert string roles to enum
    role_enums = set()
    for role in allowed_roles:
        try:
            role_enums.add(UserRole(role))
        except ValueError:
            logger.error(f"Invalid role specified: {role}")
            raise ValueError(f"Invalid role: {role}")

    async def role_checker(
        request: Request,
        user=Depends(get_current_active_user),
    ):
        """Check if user has required role."""
        request_id = getattr(request.state, "request_id", None)

        if user.role not in role_enums:
            logger.warning(
                "Role authorization failed",
                user_id=str(user.id),
                user_role=user.role.value,
                required_roles=[r.value for r in role_enums],
                endpoint=str(request.url.path),
                request_id=request_id,
            )

            audit_logger.log_event(
                event_type="auth",
                action="role_check",
                outcome="failure",
                resource_type="endpoint",
                resource_id=str(request.url.path),
                details={
                    "user_id": str(user.id),
                    "user_role": user.role.value,
                    "required_roles": [r.value for r in role_enums],
                },
            )

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "AUTHORIZATION_ERROR",
                    "message": f"Access denied. Required roles: {[r.value for r in role_enums]}",
                    "request_id": request_id,
                },
            )

        logger.debug(
            "Role authorization passed",
            user_id=str(user.id),
            role=user.role.value,
        )

        return user

    return role_checker


# Common role dependencies
RequireAdminDep = Annotated["UserModel", Depends(require_role("admin"))]
RequireTechnicianDep = Annotated["UserModel", Depends(require_role("admin", "technician"))]
RequireSupportDep = Annotated["UserModel", Depends(require_role("admin", "technician", "support"))]


async def require_admin(
    request: Request,
    user=Depends(get_current_active_user),
):
    """Require admin role."""
    from app.models.user import UserRole

    request_id = getattr(request.state, "request_id", None)

    if user.role != UserRole.ADMIN:
        logger.warning(
            "Admin access denied",
            user_id=str(user.id),
            user_role=user.role.value,
            endpoint=str(request.url.path),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "AUTHORIZATION_ERROR",
                "message": "Admin access required",
                "request_id": request_id,
            },
        )

    return user


async def require_technician_or_admin(
    request: Request,
    user=Depends(get_current_active_user),
):
    """Require technician or admin role."""
    from app.models.user import UserRole

    request_id = getattr(request.state, "request_id", None)

    if user.role not in {UserRole.ADMIN, UserRole.TECHNICIAN}:
        logger.warning(
            "Technician access denied",
            user_id=str(user.id),
            user_role=user.role.value,
            endpoint=str(request.url.path),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "AUTHORIZATION_ERROR",
                "message": "Technician or admin access required",
                "request_id": request_id,
            },
        )

    return user


# =============================================================================
# Hybrid Authentication (JWT or API Key)
# =============================================================================


async def get_current_user_or_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    bearer_credentials: Optional[str] = Depends(bearer_scheme),
    api_key: Optional[str] = Depends(api_key_header),
):
    """
    Authenticate via JWT token OR API key.

    Useful for endpoints that need to support both admin dashboard (JWT)
    and webhooks/integrations (API key).

    Returns:
        User model or API key identifier
    """
    from app.models.user import User

    request_id = getattr(request.state, "request_id", None)

    # Try JWT first
    if bearer_credentials:
        try:
            claims = decode_access_token(bearer_credentials.credentials)
            user_id = claims.get("sub")

            result = await db.execute(
                select(User).where(User.id == UUID(user_id))
            )
            user = result.scalar_one_or_none()

            if user and user.is_active:
                request.state.current_user = user
                request.state.auth_method = "jwt"
                return {"type": "user", "user": user}

        except Exception:
            pass  # Fall through to API key

    # Try API key
    if api_key and verify_admin_api_key(api_key):
        request.state.auth_method = "api_key"
        logger.debug("Authenticated via API key", endpoint=str(request.url.path))
        return {"type": "api_key", "api_key": api_key}

    # No valid authentication
    logger.warning(
        "No valid authentication provided",
        endpoint=str(request.url.path),
        has_bearer=bool(bearer_credentials),
        has_api_key=bool(api_key),
        request_id=request_id,
    )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error_code": "AUTHENTICATION_ERROR",
            "message": "Authentication required. Provide Bearer token or X-API-Key header.",
            "request_id": request_id,
        },
    )


HybridAuthDep = Annotated[dict, Depends(get_current_user_or_api_key)]


# =============================================================================
# Pagination Dependencies
# =============================================================================


class PaginationParams:
    """Pagination parameters with validation."""

    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number (1-indexed)"),
        page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    ):
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size

        logger.debug(
            "Pagination params",
            page=page,
            page_size=page_size,
            offset=self.offset,
        )


PaginationDep = Annotated[PaginationParams, Depends()]


# =============================================================================
# Device ID Validation
# =============================================================================


async def validate_device_id(
    device_id: UUID,
    request: Request,
) -> UUID:
    """
    Validate device ID format.

    Note: This only validates format. Existence check happens in service layer.

    Args:
        device_id: Device UUID from path

    Returns:
        Validated UUID
    """
    logger.debug("Device ID validated", device_id=str(device_id))
    return device_id


DeviceIdDep = Annotated[UUID, Depends(validate_device_id)]


# =============================================================================
# Customer ID Validation
# =============================================================================


async def validate_customer_id(
    customer_id: UUID,
    request: Request,
) -> UUID:
    """
    Validate customer ID format.

    Args:
        customer_id: Customer UUID from path

    Returns:
        Validated UUID
    """
    logger.debug("Customer ID validated", customer_id=str(customer_id))
    return customer_id


CustomerIdDep = Annotated[UUID, Depends(validate_customer_id)]


# =============================================================================
# Transaction ID Validation
# =============================================================================


async def validate_transaction_id(
    transaction_id: UUID,
    request: Request,
) -> UUID:
    """
    Validate transaction ID format.

    Args:
        transaction_id: Transaction UUID from path

    Returns:
        Validated UUID
    """
    logger.debug("Transaction ID validated", transaction_id=str(transaction_id))
    return transaction_id


TransactionIdDep = Annotated[UUID, Depends(validate_transaction_id)]


# =============================================================================
# Common Response Headers
# =============================================================================


def add_request_id_header(request: Request) -> dict[str, str]:
    """
    Get headers to add to response including request ID.
    """
    request_id = getattr(request.state, "request_id", None)
    headers = {}
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers
