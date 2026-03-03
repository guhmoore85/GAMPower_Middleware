"""
Structured Logging Setup for PAYGO Middleware

CRITICAL: All logging in this application follows these principles:
1. JSON structured format for machine parsing
2. Request ID correlation for tracing
3. Full context on every log entry
4. Sensitive data sanitization
5. Log rotation with retention

Usage:
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Processing payment", transaction_id="123", amount=50.00)
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# Context variable for request ID - thread-safe
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_request_id() -> Optional[str]:
    """Get current request ID from context."""
    return request_id_ctx.get()


def set_request_id(request_id: str) -> None:
    """Set request ID in context for current request."""
    request_id_ctx.set(request_id)


def clear_request_id() -> None:
    """Clear request ID from context."""
    request_id_ctx.set(None)


class SensitiveDataFilter:
    """Filter to sanitize sensitive data from logs."""

    SENSITIVE_KEYS = {
        "password",
        "secret",
        "api_key",
        "apikey",
        "token",
        "auth",
        "credential",
        "private_key",
        "secret_key",
        "encryption_key",
        "bearer",
        "authorization",
    }

    @classmethod
    def sanitize(cls, data: Any, depth: int = 0) -> Any:
        """Recursively sanitize sensitive data."""
        if depth > 10:  # Prevent infinite recursion
            return "[MAX_DEPTH_EXCEEDED]"

        if isinstance(data, dict):
            return {
                k: "[REDACTED]"
                if any(s in k.lower() for s in cls.SENSITIVE_KEYS)
                else cls.sanitize(v, depth + 1)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [cls.sanitize(item, depth + 1) for item in data]
        elif isinstance(data, str):
            # Redact anything that looks like a bearer token
            if data.lower().startswith("bearer "):
                return "Bearer [REDACTED]"
            return data
        else:
            return data


def json_formatter(record: dict) -> str:
    """
    Format log record as JSON.

    Includes:
    - timestamp: ISO format with timezone
    - level: Log level name
    - request_id: Current request ID (if set)
    - module: Module name
    - function: Function name
    - line: Line number
    - message: Log message
    - extra: Any additional context
    """
    # Get request ID from context
    req_id = get_request_id()

    # Base log entry
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": record["level"].name,
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }

    # Add request ID if available
    if req_id:
        log_entry["request_id"] = req_id

    # Add any extra fields, sanitizing sensitive data
    extra = record.get("extra", {})
    # Remove loguru internal keys
    extra = {
        k: v
        for k, v in extra.items()
        if k not in ("name", "function", "line", "module", "file", "path")
    }
    if extra:
        log_entry["context"] = SensitiveDataFilter.sanitize(extra)

    # Add exception info if present
    if record["exception"]:
        log_entry["exception"] = {
            "type": record["exception"].type.__name__ if record["exception"].type else None,
            "value": str(record["exception"].value) if record["exception"].value else None,
            "traceback": record["exception"].traceback.format() if record["exception"].traceback else None,
        }

    return json.dumps(log_entry, default=str) + "\n"


def pretty_formatter(record: dict) -> str:
    """
    Format log record for human-readable console output.
    Used in development mode.
    """
    req_id = get_request_id()
    req_id_str = f"[{req_id[:8]}]" if req_id else ""

    # Color codes by level
    level_colors = {
        "TRACE": "\033[37m",    # White
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "SUCCESS": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[35m", # Magenta
    }
    reset = "\033[0m"
    color = level_colors.get(record["level"].name, "")

    # Format extra data
    extra = record.get("extra", {})
    extra = {k: v for k, v in extra.items() if k not in ("name", "function", "line", "module", "file", "path")}
    extra_str = ""
    if extra:
        sanitized = SensitiveDataFilter.sanitize(extra)
        extra_str = " | " + " ".join(f"{k}={v}" for k, v in sanitized.items())

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    return (
        f"{timestamp} | {color}{record['level'].name:8}{reset} | "
        f"{record['name']}:{record['function']}:{record['line']} | "
        f"{req_id_str} {record['message']}{extra_str}\n"
    )


def setup_logging(
    level: str = "INFO",
    log_format: str = "json",
    log_file_path: Optional[str] = None,
    rotation_size: str = "50 MB",
    retention_count: int = 10,
    enable_sql_logging: bool = False,
) -> None:
    """
    Configure logging for the application.

    CRITICAL: This must be called at application startup.
    If not called, logs may not be properly structured.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: "json" for production, "pretty" for development
        log_file_path: Path to log file (optional)
        rotation_size: When to rotate logs (e.g., "50 MB")
        retention_count: Number of log files to keep
        enable_sql_logging: Enable SQLAlchemy query logging
    """
    # Remove default handler
    logger.remove()

    # Choose formatter
    if log_format == "json":
        format_func = json_formatter
    else:
        format_func = pretty_formatter

    # Use sink functions so loguru doesn't try to parse our output as format strings
    def stderr_sink(message):
        record = message.record
        sys.stderr.write(format_func(record))

    def file_sink(message):
        record = message.record
        return json_formatter(record)

    # Add console handler
    logger.add(
        stderr_sink,
        level=level,
        colorize=False,
        backtrace=True,
        diagnose=True,
    )

    # Add file handler if path specified
    if log_file_path:
        # Ensure directory exists
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_file_path,
            format="{message}",
            level=level,
            rotation=rotation_size,
            retention=retention_count,
            compression="gz",
            backtrace=True,
            diagnose=True,
            serialize=True,
        )

    # Configure SQLAlchemy logging
    if enable_sql_logging:
        # Set up SQLAlchemy logger to route through loguru
        sql_logger = logging.getLogger("sqlalchemy.engine")
        sql_logger.setLevel(logging.DEBUG if enable_sql_logging else logging.WARNING)

        # Add handler to route to loguru
        class InterceptHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    level = logger.level(record.levelname).name
                except ValueError:
                    level = record.levelno

                frame, depth = logging.currentframe(), 2
                while frame and frame.f_code.co_filename == logging.__file__:
                    frame = frame.f_back
                    depth += 1

                logger.opt(depth=depth, exception=record.exc_info).log(
                    level, record.getMessage()
                )

        sql_logger.addHandler(InterceptHandler())

    # Log startup
    logger.info(
        "Logging initialized",
        level=level,
        format=log_format,
        file_path=log_file_path,
        rotation_size=rotation_size,
        retention_count=retention_count,
        sql_logging=enable_sql_logging,
    )


@lru_cache(maxsize=128)
def get_logger(name: str) -> "BoundLogger":
    """
    Get a logger instance for a module.

    Returns a logger bound to the module name for proper log attribution.

    Usage:
        logger = get_logger(__name__)
        logger.info("Something happened", extra_field="value")
    """
    return BoundLogger(name)


class BoundLogger:
    """
    Logger wrapper that binds module context to all log calls.

    Provides methods: trace, debug, info, success, warning, error, critical, exception
    All methods accept keyword arguments for extra context.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._logger = logger.bind(name=name)

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        """Internal log method with sanitization."""
        sanitized = SensitiveDataFilter.sanitize(kwargs)
        getattr(self._logger, level)(message, **sanitized)

    def trace(self, message: str, **kwargs: Any) -> None:
        """Log at TRACE level."""
        self._log("trace", message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log at DEBUG level."""
        self._log("debug", message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log at INFO level."""
        self._log("info", message, **kwargs)

    def success(self, message: str, **kwargs: Any) -> None:
        """Log at SUCCESS level."""
        self._log("success", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log at WARNING level."""
        self._log("warning", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log at ERROR level - use for errors that need attention."""
        self._log("error", message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log at CRITICAL level - use for fatal errors."""
        self._log("critical", message, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception with full traceback."""
        sanitized = SensitiveDataFilter.sanitize(kwargs)
        self._logger.opt(exception=True).error(message, **sanitized)

    def bind(self, **kwargs: Any) -> "BoundLogger":
        """Create a new logger with additional bound context."""
        new_logger = BoundLogger(self.name)
        new_logger._logger = self._logger.bind(**SensitiveDataFilter.sanitize(kwargs))
        return new_logger


# Audit logger for compliance-relevant events
class AuditLogger:
    """
    Special logger for audit events.

    Use this for:
    - User authentication events
    - Permission changes
    - Data access events
    - Financial transactions
    - Configuration changes

    All audit logs are written with extra context for compliance.
    """

    def __init__(self) -> None:
        self._logger = get_logger("audit")

    def log_event(
        self,
        event_type: str,
        action: str,
        *,
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        outcome: str = "success",
        details: Optional[dict] = None,
    ) -> None:
        """
        Log an audit event.

        Args:
            event_type: Type of event (auth, access, config, transaction)
            action: Action performed (create, read, update, delete, login, etc.)
            actor_id: ID of user/system performing action
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            outcome: "success" or "failure"
            details: Additional details about the event
        """
        self._logger.info(
            f"AUDIT: {event_type}.{action}",
            event_type=event_type,
            action=action,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
            audit=True,  # Mark as audit event for filtering
        )


# Global audit logger instance
audit_logger = AuditLogger()


# Performance logger for slow operations
class PerformanceLogger:
    """
    Logger for tracking operation performance.

    Use this to track:
    - Slow database queries
    - External API response times
    - Request latency
    """

    def __init__(self, threshold_ms: float = 1000) -> None:
        """
        Args:
            threshold_ms: Log warning if operation takes longer than this
        """
        self._logger = get_logger("performance")
        self.threshold_ms = threshold_ms

    def log_operation(
        self,
        operation: str,
        duration_ms: float,
        *,
        success: bool = True,
        details: Optional[dict] = None,
    ) -> None:
        """
        Log operation performance.

        Args:
            operation: Name of the operation
            duration_ms: Time taken in milliseconds
            success: Whether operation succeeded
            details: Additional context
        """
        log_data = {
            "operation": operation,
            "duration_ms": round(duration_ms, 2),
            "success": success,
            **(details or {}),
        }

        if duration_ms > self.threshold_ms:
            self._logger.warning(
                f"SLOW OPERATION: {operation} took {duration_ms:.2f}ms",
                **log_data,
            )
        else:
            self._logger.debug(
                f"Operation {operation} completed in {duration_ms:.2f}ms",
                **log_data,
            )


# Global performance logger instance
perf_logger = PerformanceLogger()
