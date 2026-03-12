"""
Payment Provider Factory

Creates payment provider instances based on configuration.

CRITICAL: Logs provider initialization.
"""

from functools import lru_cache
from typing import Optional

from app.core.config import settings
from app.core.exceptions import PaymentProviderError
from app.core.logging import get_logger
from app.models.transaction import PaymentProvider as PaymentProviderEnum
from app.services.payment.apple_pay_provider import ApplePayPaymentProvider
from app.services.payment.base import PaymentProvider
from app.services.payment.qmoney_provider import QMoneyPaymentProvider
from app.services.payment.wave_provider import WavePaymentProvider

logger = get_logger(__name__)


class PaymentProviderFactory:
    """
    Factory for creating payment provider instances.

    Caches provider instances for reuse.
    Logs all provider creation.
    """

    _providers: dict[str, PaymentProvider] = {}

    @classmethod
    def get_provider(
        cls,
        provider: PaymentProviderEnum | str,
    ) -> PaymentProvider:
        """
        Get payment provider instance.

        Args:
            provider: Provider enum or string name

        Returns:
            PaymentProvider instance

        Raises:
            PaymentProviderError: If provider is not supported
        """
        if isinstance(provider, PaymentProviderEnum):
            provider_name = provider.value
        else:
            provider_name = provider.lower()

        # Check cache
        if provider_name in cls._providers:
            logger.debug(
                "Using cached payment provider",
                provider=provider_name,
            )
            return cls._providers[provider_name]

        # Create new provider
        provider_instance = cls._create_provider(provider_name)
        cls._providers[provider_name] = provider_instance

        logger.info(
            "Payment provider created and cached",
            provider=provider_name,
        )

        return provider_instance

    @classmethod
    def _create_provider(cls, provider_name: str) -> PaymentProvider:
        """
        Create new payment provider instance.

        LOUD: Logs creation with configuration details (not secrets).
        """
        logger.debug(
            "Creating payment provider",
            provider=provider_name,
        )

        if provider_name == "wave":
            return WavePaymentProvider(
                api_key=settings.wave_api_key,
                api_url=settings.wave_api_url,
                webhook_secret=settings.wave_webhook_secret,
                checkout_api_key=settings.wave_checkout_api_key,
                balance_api_key=settings.wave_balance_api_key,
                payout_api_key=settings.wave_payout_api_key,
            )

        elif provider_name == "qmoney":
            return QMoneyPaymentProvider(
                api_key=settings.qmoney_api_key,
                api_url=settings.qmoney_api_url,
                webhook_secret=settings.qmoney_webhook_secret,
            )

        elif provider_name == "apple_pay":
            return ApplePayPaymentProvider(
                api_key="",  # Apple Pay uses certificates instead
                api_url="",
                webhook_secret="",
                merchant_id=settings.apple_pay_merchant_id,
                certificate_path=settings.apple_pay_certificate_path,
                private_key_path=settings.apple_pay_private_key_path,
            )

        else:
            logger.error(
                "Unsupported payment provider",
                provider=provider_name,
            )
            raise PaymentProviderError(
                provider=provider_name,
                operation="create",
                provider_message=f"Provider '{provider_name}' is not supported",
            )

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached providers. Useful for testing."""
        cls._providers.clear()
        logger.debug("Payment provider cache cleared")

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """Get list of supported payment providers."""
        return [p.value for p in PaymentProviderEnum]


# Convenience function for getting providers
def get_payment_provider(
    provider: PaymentProviderEnum | str,
) -> PaymentProvider:
    """
    Get payment provider instance.

    Args:
        provider: Provider enum or string name

    Returns:
        PaymentProvider instance
    """
    return PaymentProviderFactory.get_provider(provider)
