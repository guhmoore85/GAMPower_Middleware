"""
PAYGO Middleware FastAPI Application

Main application entry point with:
- Request ID middleware for tracing
- Global exception handling
- Health checks
- API versioning
- CORS configuration

CRITICAL: All startup validation is LOUD.
The application will fail to start if any dependency is unavailable.
"""

import time
from contextlib import asynccontextmanager
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1 import router as api_v1_router
from app.core.config import settings, validate_startup_config
from app.core.exceptions import BasePaygoError
from app.core.logging import (
    clear_request_id,
    get_logger,
    perf_logger,
    set_request_id,
    setup_logging,
)
from app.db.session import close_db, health_check as db_health_check, init_db

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan management.

    Handles:
    - Startup: Validate config, connect to DB, initialize services
    - Shutdown: Close connections, cleanup resources

    LOUD: Fails immediately if any startup check fails.
    """
    # =========================================================================
    # STARTUP
    # =========================================================================
    logger.info(
        "Starting PAYGO Middleware",
        version=__version__,
        environment=settings.environment,
    )

    # Setup logging
    setup_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        log_file_path=settings.log_file_path,
        rotation_size=settings.log_rotation_size,
        retention_count=settings.log_retention_count,
        enable_sql_logging=settings.log_sql_queries,
    )

    # Validate configuration
    logger.info("Validating configuration...")
    try:
        config_result = validate_startup_config()
        for warning in config_result.get("warnings", []):
            logger.warning(f"Configuration warning: {warning}")
        logger.info("Configuration validated successfully")
    except Exception as e:
        logger.critical(f"Configuration validation failed: {e}")
        raise

    # Initialize database
    logger.info("Connecting to database...")
    try:
        await init_db()
        logger.info("Database connection established")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        raise

    # TODO: Initialize Redis connection
    # TODO: Initialize scheduler if enabled

    logger.info(
        "PAYGO Middleware started successfully",
        version=__version__,
        environment=settings.environment,
        debug=settings.debug,
    )

    yield

    # =========================================================================
    # SHUTDOWN
    # =========================================================================
    logger.info("Shutting down PAYGO Middleware...")

    # Close database connections
    await close_db()

    # TODO: Close Redis connections
    # TODO: Stop scheduler

    logger.info("PAYGO Middleware shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="PAYGO Middleware API",
    description=(
        "Middleware platform connecting payment systems (Wave, QMoney, Apple Pay) "
        "to IoT devices (solar, e-mobility) using OpenPAYGO protocols."
    ),
    version=__version__,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)

# Configure CORS
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# =============================================================================
# MIDDLEWARE
# =============================================================================


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Callable) -> Response:
    """
    Add request ID to all requests for tracing.

    - Uses X-Request-ID header if provided
    - Generates new UUID if not provided
    - Adds to response headers
    - Sets in logging context
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    set_request_id(request_id)

    # Store in request state
    request.state.request_id = request_id

    # Log request
    logger.debug(
        "Request started",
        method=request.method,
        path=str(request.url.path),
        request_id=request_id,
    )

    # Track timing
    start_time = time.perf_counter()

    try:
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Add request ID to response
        response.headers["X-Request-ID"] = request_id

        # Log response
        logger.debug(
            "Request completed",
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            request_id=request_id,
        )

        # Log slow requests
        perf_logger.log_operation(
            operation=f"{request.method} {request.url.path}",
            duration_ms=duration_ms,
        )

        return response

    except Exception as e:
        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        logger.error(
            "Request failed with exception",
            method=request.method,
            path=str(request.url.path),
            error=str(e),
            duration_ms=round(duration_ms, 2),
            request_id=request_id,
        )
        raise

    finally:
        clear_request_id()


# =============================================================================
# EXCEPTION HANDLERS
# =============================================================================


@app.exception_handler(BasePaygoError)
async def paygo_exception_handler(request: Request, exc: BasePaygoError) -> JSONResponse:
    """
    Handle PAYGO custom exceptions.

    LOUD: Logs all exceptions with full context.
    """
    request_id = getattr(request.state, "request_id", None)

    logger.error(
        "PAYGO error",
        error_code=exc.error_code,
        message=exc.message,
        request_id=request_id,
        context=exc.context,
    )

    return JSONResponse(
        status_code=exc.http_status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "timestamp": exc.timestamp.isoformat(),
            "request_id": request_id,
            "details": exc._sanitize_context(),
        },
        headers={"X-Request-ID": request_id} if request_id else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle Pydantic validation errors.

    LOUD: Logs all validation failures with field details.
    """
    request_id = getattr(request.state, "request_id", None)

    # Extract field errors
    field_errors = []
    for error in exc.errors():
        field_errors.append({
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        })

    logger.warning(
        "Validation error",
        request_id=request_id,
        path=str(request.url.path),
        field_errors=field_errors,
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "request_id": request_id,
            "field_errors": field_errors,
        },
        headers={"X-Request-ID": request_id} if request_id else None,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    Handle HTTP exceptions.
    """
    request_id = getattr(request.state, "request_id", None)

    # Log based on status code
    if exc.status_code >= 500:
        logger.error(
            "HTTP error",
            status_code=exc.status_code,
            detail=exc.detail,
            request_id=request_id,
        )
    elif exc.status_code >= 400:
        logger.warning(
            "HTTP error",
            status_code=exc.status_code,
            detail=exc.detail,
            request_id=request_id,
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else None,
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions.

    LOUD: Logs full stack trace for debugging.
    """
    import traceback

    request_id = getattr(request.state, "request_id", None)

    logger.error(
        "Unexpected error",
        error=str(exc),
        error_type=type(exc).__name__,
        request_id=request_id,
        stack_trace=traceback.format_exc(),
    )

    # In production, don't expose internal errors
    if settings.is_production:
        message = "An unexpected error occurred"
    else:
        message = f"{type(exc).__name__}: {str(exc)}"

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": "INTERNAL_ERROR",
            "message": message,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else None,
    )


# =============================================================================
# HEALTH CHECKS
# =============================================================================


@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    """
    Health check endpoint.

    Validates:
    - Database connectivity
    - Redis connectivity (TODO)
    - Overall application health

    Returns 200 if healthy, 503 if any component is unhealthy.
    """
    components = {}

    # Check database
    db_status = await db_health_check()
    components["database"] = db_status

    # TODO: Check Redis
    components["redis"] = {"status": "healthy", "latency_ms": 0}  # Mock for now

    # Determine overall status
    all_healthy = all(c.get("status") == "healthy" for c in components.values())
    overall_status = "healthy" if all_healthy else "unhealthy"

    response = {
        "status": overall_status,
        "version": __version__,
        "environment": settings.environment,
        "components": components,
    }

    if not all_healthy:
        logger.warning("Health check failed", components=components)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response,
        )

    return response


@app.get("/ready", tags=["Health"])
async def readiness_check() -> dict:
    """
    Readiness check for Kubernetes.

    Returns 200 if ready to accept traffic.
    """
    return {"status": "ready", "version": __version__}


@app.get("/live", tags=["Health"])
async def liveness_check() -> dict:
    """
    Liveness check for Kubernetes.

    Returns 200 if application is alive.
    """
    return {"status": "alive", "version": __version__}


# =============================================================================
# ROOT
# =============================================================================


@app.get("/", tags=["Root"])
async def root() -> dict:
    """API root - returns basic info."""
    return {
        "name": "PAYGO Middleware API",
        "version": __version__,
        "docs": "/docs" if settings.debug else None,
    }


# =============================================================================
# INCLUDE API ROUTERS
# =============================================================================

app.include_router(api_v1_router, prefix="/api/v1")
