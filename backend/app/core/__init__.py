"""
Core module containing configuration, security, logging, and exception handling.

All modules in this package are designed to FAIL LOUDLY with comprehensive
error handling, logging, and validation.
"""

from app.core.config import settings
from app.core.exceptions import (
    BasePaygoError,
    DatabaseError,
    DeviceNotFoundError,
    InvalidTokenError,
    PaymentProviderError,
    TriggerEvaluationError,
    ValidationError,
)
from app.core.logging import get_logger, setup_logging

__all__ = [
    "settings",
    "get_logger",
    "setup_logging",
    "BasePaygoError",
    "DatabaseError",
    "DeviceNotFoundError",
    "InvalidTokenError",
    "PaymentProviderError",
    "TriggerEvaluationError",
    "ValidationError",
]
