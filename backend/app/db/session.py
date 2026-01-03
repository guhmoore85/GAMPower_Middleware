"""
Database Session Management

Provides async database session and connection management.

CRITICAL: All database errors are logged LOUDLY with full context.
Connection failures trigger retries before failing.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.exceptions import DatabaseError, ServiceUnavailableError
from app.core.logging import get_logger, perf_logger

logger = get_logger(__name__)

# Connection retry configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """
    Get or create the async database engine.

    LOUD: Logs engine creation and configuration.

    Returns:
        AsyncEngine instance

    Raises:
        DatabaseError: If engine creation fails
    """
    global _engine

    if _engine is not None:
        return _engine

    try:
        logger.info(
            "Creating database engine",
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )

        _engine = create_async_engine(
            str(settings.database_url),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            pool_pre_ping=True,  # Verify connections before use
            echo=settings.log_sql_queries,
            future=True,
        )

        # Add event listeners for logging
        if settings.log_sql_queries:
            _setup_query_logging(_engine.sync_engine)

        logger.info("Database engine created successfully")
        return _engine

    except Exception as e:
        logger.critical(
            "Failed to create database engine",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise DatabaseError(
            operation="create_engine",
            original_error=e,
        )


def _setup_query_logging(engine) -> None:
    """Set up SQL query logging."""

    @event.listens_for(engine, "before_cursor_execute")
    def receive_before_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        import time
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def receive_after_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        import time
        total = (time.perf_counter() - conn.info["query_start_time"].pop(-1)) * 1000

        # Log slow queries as warnings
        if total > 100:  # > 100ms
            logger.warning(
                "Slow SQL query",
                duration_ms=round(total, 2),
                statement=statement[:500],  # Truncate long queries
            )
        else:
            logger.debug(
                "SQL query executed",
                duration_ms=round(total, 2),
                statement=statement[:200],
            )


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get or create the async session factory.

    Returns:
        Session factory for creating database sessions
    """
    global _session_factory

    if _session_factory is not None:
        return _session_factory

    engine = get_engine()

    _session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    logger.info("Database session factory created")
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session.

    LOUD: Logs session lifecycle and errors.

    Yields:
        AsyncSession bound to request lifecycle

    Raises:
        DatabaseError: If session creation fails
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as e:
            logger.error(
                "Database error - rolling back",
                error=str(e),
                error_type=type(e).__name__,
            )
            await session.rollback()
            raise DatabaseError(
                operation="session_commit",
                original_error=e,
            )
        except Exception as e:
            logger.error(
                "Unexpected error - rolling back",
                error=str(e),
                error_type=type(e).__name__,
            )
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_session_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database sessions.

    Use this for operations outside of FastAPI request context.

    Example:
        async with get_db_session_context() as session:
            result = await session.execute(query)
    """
    async for session in get_db_session():
        yield session


async def init_db(retries: int = MAX_RETRIES) -> bool:
    """
    Initialize database connection and verify connectivity.

    LOUD: Logs all connection attempts and failures.

    Args:
        retries: Number of retry attempts

    Returns:
        True if connection successful

    Raises:
        ServiceUnavailableError: If database is unavailable after retries
    """
    logger.info("Initializing database connection", max_retries=retries)

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            engine = get_engine()

            async with engine.connect() as conn:
                # Execute simple query to verify connection
                result = await conn.execute(text("SELECT 1"))
                result.fetchone()

            logger.info(
                "Database connection verified",
                attempt=attempt,
            )
            return True

        except (OperationalError, InterfaceError) as e:
            last_error = e
            logger.warning(
                "Database connection attempt failed",
                attempt=attempt,
                max_retries=retries,
                error=str(e),
                error_type=type(e).__name__,
            )

            if attempt < retries:
                delay = RETRY_DELAY_SECONDS * attempt
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        except Exception as e:
            last_error = e
            logger.error(
                "Unexpected error during database initialization",
                error=str(e),
                error_type=type(e).__name__,
            )
            break

    # All retries exhausted
    logger.critical(
        "Database initialization failed after all retries",
        retries=retries,
        error=str(last_error),
    )

    raise ServiceUnavailableError(
        service="database",
        reason=f"Failed to connect after {retries} attempts: {last_error}",
        original_error=last_error,
    )


async def close_db() -> None:
    """
    Close database connections.

    Call this during application shutdown.
    """
    global _engine, _session_factory

    if _engine is not None:
        logger.info("Closing database engine")
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine closed")


async def health_check() -> dict:
    """
    Check database health.

    Returns:
        Health check result dict

    LOUD: Logs health check failures.
    """
    try:
        engine = get_engine()

        import time
        start = time.perf_counter()

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()

        duration_ms = (time.perf_counter() - start) * 1000

        perf_logger.log_operation(
            operation="db_health_check",
            duration_ms=duration_ms,
        )

        return {
            "status": "healthy",
            "latency_ms": round(duration_ms, 2),
        }

    except Exception as e:
        logger.error(
            "Database health check failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        return {
            "status": "unhealthy",
            "error": str(e),
        }
