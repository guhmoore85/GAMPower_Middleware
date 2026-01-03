"""
SQLAlchemy Models for PAYGO Middleware

All models include:
- Comprehensive field validation
- Proper indexing for queries
- Relationship definitions
- LOUD validation errors
"""

from app.models.customer import Customer
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_activation import ActivationType, DeviceActivation
from app.models.device_metric import DeviceMetric, MetricType
from app.models.payment_trigger import PaymentTrigger, TriggerType
from app.models.transaction import PaymentProvider, Transaction, TransactionStatus

__all__ = [
    # Enums
    "DeviceType",
    "DeviceStatus",
    "PaymentProvider",
    "TransactionStatus",
    "MetricType",
    "ActivationType",
    "TriggerType",
    # Models
    "Device",
    "Customer",
    "Transaction",
    "DeviceMetric",
    "DeviceActivation",
    "PaymentTrigger",
]
