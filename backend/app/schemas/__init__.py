"""
Pydantic Schemas for Request/Response Validation

All schemas use strict mode for rigorous validation.
CRITICAL: Validation errors are detailed and actionable.
"""

from app.schemas.customer import (
    CustomerCreate,
    CustomerResponse,
    CustomerUpdate,
)
from app.schemas.device import (
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)
from app.schemas.device_activation import (
    ActivationCreate,
    ActivationResponse,
)
from app.schemas.device_metric import (
    MetricCreate,
    MetricResponse,
    MetricBatchCreate,
)
from app.schemas.payment_trigger import (
    TriggerCreate,
    TriggerResponse,
    TriggerUpdate,
)
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
)
from app.schemas.common import (
    ErrorResponse,
    HealthResponse,
    PaginatedResponse,
)

__all__ = [
    # Customer
    "CustomerCreate",
    "CustomerResponse",
    "CustomerUpdate",
    # Device
    "DeviceCreate",
    "DeviceResponse",
    "DeviceUpdate",
    # Activation
    "ActivationCreate",
    "ActivationResponse",
    # Metric
    "MetricCreate",
    "MetricResponse",
    "MetricBatchCreate",
    # Trigger
    "TriggerCreate",
    "TriggerResponse",
    "TriggerUpdate",
    # Transaction
    "TransactionCreate",
    "TransactionResponse",
    "TransactionUpdate",
    # Common
    "ErrorResponse",
    "HealthResponse",
    "PaginatedResponse",
]
