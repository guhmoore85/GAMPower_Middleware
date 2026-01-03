"""
Payment Provider Module

Provides abstraction layer for payment providers:
- Wave
- QMoney
- Apple Pay

CRITICAL: All payment operations are logged loudly.
Mock implementations are clearly marked.
"""

from app.services.payment.base import PaymentProvider, PaymentStatus
from app.services.payment.factory import PaymentProviderFactory
from app.services.payment.wave_provider import WavePaymentProvider
from app.services.payment.qmoney_provider import QMoneyPaymentProvider
from app.services.payment.apple_pay_provider import ApplePayPaymentProvider

__all__ = [
    "PaymentProvider",
    "PaymentStatus",
    "PaymentProviderFactory",
    "WavePaymentProvider",
    "QMoneyPaymentProvider",
    "ApplePayPaymentProvider",
]
