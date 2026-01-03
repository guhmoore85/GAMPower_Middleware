"""
FastAPI Dependencies for PAYGO Middleware

This module provides dependency injection for:
- Database sessions
- Authentication/Authorization
- Request ID tracking
- Redis connections
- Common request parameters

CRITICAL: All dependencies validate their requirements loudly.
"""

from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.security import APIKeyHeader, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
)
from app.core.logging import get_logger, set_request_id
from app.core.security import decode_access_token, verify_admin_api_key
from app.db.session import get_db_session

logger = get_logger(__name__)

# Security schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


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
# API Key Authentication
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
# Admin Authentication
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


async def get_current_user(
    request: Request,
    credentials: Optional[str] = Depends(bearer_scheme),
) -> dict:
    """
    Get current user from JWT token.

    LOUD: Logs all token validation attempts.

    Returns:
        Token claims including user ID

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
            status_code=401,
            detail={
                "error_code": "AUTHENTICATION_ERROR",
                "message": "Bearer token required",
                "request_id": request_id,
            },
        )

    try:
        claims = decode_access_token(credentials.credentials)
        logger.debug("User authenticated", user_id=claims.get("sub"))
        return claims

    except AuthenticationError as e:
        logger.warning(
            "JWT validation failed",
            endpoint=str(request.url.path),
            error=str(e),
            request_id=request_id,
        )
        raise HTTPException(
            status_code=401,
            detail=e.to_dict(),
        )


CurrentUserDep = Annotated[dict, Depends(get_current_user)]


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
