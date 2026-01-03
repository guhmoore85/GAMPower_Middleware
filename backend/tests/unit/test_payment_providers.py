"""
Unit Tests for Payment Providers

Tests payment provider factory and individual provider implementations.

CRITICAL: Each test asserts with clear failure messages.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import PaymentProviderError
from app.models.transaction import PaymentProvider as PaymentProviderEnum
from app.services.payment.base import PaymentProvider, PaymentResult
from app.services.payment.factory import PaymentProviderFactory, get_payment_provider


class TestPaymentProviderFactory:
    """Tests for PaymentProviderFactory."""

    def setup_method(self):
        """Clear factory cache before each test."""
        PaymentProviderFactory.clear_cache()

    def test_get_wave_provider(self):
        """Test getting Wave provider."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.wave_api_key = "test_key"
            mock_settings.wave_api_url = "https://api.wave.com"
            mock_settings.wave_webhook_secret = "webhook_secret"

            provider = PaymentProviderFactory.get_provider("wave")

            assert provider is not None, "Wave provider should be created"
            assert provider.provider_name == "wave", (
                f"Provider name should be 'wave', got '{provider.provider_name}'"
            )

    def test_get_qmoney_provider(self):
        """Test getting QMoney provider."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.qmoney_api_key = "test_key"
            mock_settings.qmoney_api_url = "https://api.qmoney.com"
            mock_settings.qmoney_webhook_secret = "webhook_secret"

            provider = PaymentProviderFactory.get_provider("qmoney")

            assert provider is not None, "QMoney provider should be created"
            assert provider.provider_name == "qmoney", (
                f"Provider name should be 'qmoney', got '{provider.provider_name}'"
            )

    def test_get_apple_pay_provider(self):
        """Test getting Apple Pay provider."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.apple_pay_merchant_id = "merchant.test.com"
            mock_settings.apple_pay_certificate_path = "/path/to/cert"
            mock_settings.apple_pay_private_key_path = "/path/to/key"

            provider = PaymentProviderFactory.get_provider("apple_pay")

            assert provider is not None, "Apple Pay provider should be created"
            assert provider.provider_name == "apple_pay", (
                f"Provider name should be 'apple_pay', got '{provider.provider_name}'"
            )

    def test_unsupported_provider_raises_error(self):
        """Test that unsupported provider raises error."""
        with pytest.raises(PaymentProviderError) as exc_info:
            PaymentProviderFactory.get_provider("unsupported_provider")

        assert "not supported" in str(exc_info.value).lower(), (
            "Error should mention provider not supported"
        )

    def test_provider_caching(self):
        """Test that providers are cached."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.wave_api_key = "test_key"
            mock_settings.wave_api_url = "https://api.wave.com"
            mock_settings.wave_webhook_secret = "webhook_secret"

            provider1 = PaymentProviderFactory.get_provider("wave")
            provider2 = PaymentProviderFactory.get_provider("wave")

            assert provider1 is provider2, "Same provider instance should be returned"

    def test_get_provider_from_enum(self):
        """Test getting provider using enum value."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.wave_api_key = "test_key"
            mock_settings.wave_api_url = "https://api.wave.com"
            mock_settings.wave_webhook_secret = "webhook_secret"

            provider = PaymentProviderFactory.get_provider(PaymentProviderEnum.WAVE)

            assert provider.provider_name == "wave", (
                "Should create wave provider from enum"
            )

    def test_clear_cache(self):
        """Test cache clearing."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.wave_api_key = "test_key"
            mock_settings.wave_api_url = "https://api.wave.com"
            mock_settings.wave_webhook_secret = "webhook_secret"

            provider1 = PaymentProviderFactory.get_provider("wave")
            PaymentProviderFactory.clear_cache()
            provider2 = PaymentProviderFactory.get_provider("wave")

            assert provider1 is not provider2, (
                "New instance should be created after cache clear"
            )

    def test_get_supported_providers(self):
        """Test listing supported providers."""
        providers = PaymentProviderFactory.get_supported_providers()

        assert "wave" in providers, "wave should be supported"
        assert "qmoney" in providers, "qmoney should be supported"
        assert "apple_pay" in providers, "apple_pay should be supported"

    def test_convenience_function(self):
        """Test get_payment_provider convenience function."""
        with patch("app.services.payment.factory.settings") as mock_settings:
            mock_settings.wave_api_key = "test_key"
            mock_settings.wave_api_url = "https://api.wave.com"
            mock_settings.wave_webhook_secret = "webhook_secret"

            provider = get_payment_provider("wave")

            assert provider is not None, "Convenience function should work"


class TestPaymentProviderBase:
    """Tests for base PaymentProvider functionality."""

    def test_payment_result_success(self):
        """Test PaymentResult for successful payment."""
        result = PaymentResult(
            success=True,
            provider_transaction_id="txn_123",
            amount=Decimal("50.00"),
            currency="USD",
            status="completed",
        )

        assert result.success, "Result should indicate success"
        assert result.provider_transaction_id == "txn_123"
        assert result.amount == Decimal("50.00")
        assert result.error_message is None, "No error for successful payment"

    def test_payment_result_failure(self):
        """Test PaymentResult for failed payment."""
        result = PaymentResult(
            success=False,
            provider_transaction_id=None,
            amount=Decimal("50.00"),
            currency="USD",
            status="failed",
            error_message="Insufficient funds",
            error_code="INSUFFICIENT_FUNDS",
        )

        assert not result.success, "Result should indicate failure"
        assert result.error_message == "Insufficient funds"
        assert result.error_code == "INSUFFICIENT_FUNDS"


class TestWaveProvider:
    """Tests for Wave payment provider."""

    @pytest.fixture
    def wave_provider(self):
        """Create Wave provider for testing."""
        from app.services.payment.wave_provider import WavePaymentProvider

        return WavePaymentProvider(
            api_key="test_api_key",
            api_url="https://api.wave.com",
            webhook_secret="test_webhook_secret",
        )

    @pytest.mark.asyncio
    async def test_initiate_payment(self, wave_provider):
        """Test payment initiation."""
        result = await wave_provider.initiate_payment(
            amount=Decimal("50.00"),
            currency="USD",
            customer_phone="+1234567890",
            reference="test_ref_123",
        )

        assert result.success, f"Payment should succeed, error: {result.error_message}"
        assert result.provider_transaction_id is not None, (
            "Should have transaction ID"
        )
        assert result.status == "pending", (
            "Initial status should be pending"
        )

    @pytest.mark.asyncio
    async def test_check_status(self, wave_provider):
        """Test status check."""
        # First initiate a payment
        init_result = await wave_provider.initiate_payment(
            amount=Decimal("50.00"),
            currency="USD",
            customer_phone="+1234567890",
            reference="test_ref_123",
        )

        # Check status
        status_result = await wave_provider.check_status(
            transaction_id=init_result.provider_transaction_id,
        )

        assert status_result.success, "Status check should succeed"
        assert status_result.status in ["pending", "completed", "failed"], (
            f"Invalid status: {status_result.status}"
        )

    @pytest.mark.asyncio
    async def test_verify_webhook(self, wave_provider):
        """Test webhook verification."""
        payload = b'{"event": "payment.completed", "data": {"id": "123"}}'

        # Generate valid signature
        import hashlib
        import hmac
        valid_signature = hmac.new(
            b"test_webhook_secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        is_valid = await wave_provider.verify_webhook(
            payload=payload,
            signature=valid_signature,
        )

        assert is_valid, "Valid webhook should be verified"

    @pytest.mark.asyncio
    async def test_invalid_webhook_rejected(self, wave_provider):
        """Test invalid webhook is rejected."""
        payload = b'{"event": "payment.completed", "data": {"id": "123"}}'

        is_valid = await wave_provider.verify_webhook(
            payload=payload,
            signature="invalid_signature",
        )

        assert not is_valid, "Invalid webhook should be rejected"


class TestQMoneyProvider:
    """Tests for QMoney payment provider."""

    @pytest.fixture
    def qmoney_provider(self):
        """Create QMoney provider for testing."""
        from app.services.payment.qmoney_provider import QMoneyPaymentProvider

        return QMoneyPaymentProvider(
            api_key="test_api_key",
            api_url="https://api.qmoney.com",
            webhook_secret="test_webhook_secret",
        )

    @pytest.mark.asyncio
    async def test_initiate_payment(self, qmoney_provider):
        """Test payment initiation."""
        result = await qmoney_provider.initiate_payment(
            amount=Decimal("100.00"),
            currency="GNF",
            customer_phone="+224123456789",
            reference="test_ref_456",
        )

        assert result.success, f"Payment should succeed, error: {result.error_message}"
        assert result.provider_transaction_id is not None

    @pytest.mark.asyncio
    async def test_refund(self, qmoney_provider):
        """Test refund processing."""
        # First initiate a payment
        init_result = await qmoney_provider.initiate_payment(
            amount=Decimal("100.00"),
            currency="GNF",
            customer_phone="+224123456789",
            reference="test_ref_456",
        )

        # Process refund
        refund_result = await qmoney_provider.refund(
            transaction_id=init_result.provider_transaction_id,
            amount=Decimal("50.00"),
            reason="Customer request",
        )

        assert refund_result.success, "Refund should succeed"
        assert refund_result.amount == Decimal("50.00")


class TestApplePayProvider:
    """Tests for Apple Pay payment provider."""

    @pytest.fixture
    def apple_pay_provider(self):
        """Create Apple Pay provider for testing."""
        from app.services.payment.apple_pay_provider import ApplePayPaymentProvider

        return ApplePayPaymentProvider(
            api_key="",
            api_url="",
            webhook_secret="",
            merchant_id="merchant.test.com",
            certificate_path="/path/to/cert",
            private_key_path="/path/to/key",
        )

    @pytest.mark.asyncio
    async def test_initiate_payment(self, apple_pay_provider):
        """Test payment initiation."""
        result = await apple_pay_provider.initiate_payment(
            amount=Decimal("75.00"),
            currency="USD",
            customer_phone="+1234567890",
            reference="test_ref_789",
            payment_token="mock_apple_pay_token",
        )

        assert result.success, f"Payment should succeed, error: {result.error_message}"

    @pytest.mark.asyncio
    async def test_validate_merchant(self, apple_pay_provider):
        """Test merchant validation."""
        validation_data = await apple_pay_provider.validate_merchant(
            validation_url="https://apple-pay-gateway.apple.com/paymentservices/startSession",
            domain_name="example.com",
        )

        assert validation_data is not None, "Should return validation data"
        assert "merchantSession" in validation_data, (
            "Should contain merchant session"
        )


class TestPaymentProviderErrors:
    """Tests for payment provider error handling."""

    @pytest.fixture
    def wave_provider(self):
        """Create Wave provider for testing."""
        from app.services.payment.wave_provider import WavePaymentProvider

        return WavePaymentProvider(
            api_key="test_api_key",
            api_url="https://api.wave.com",
            webhook_secret="test_webhook_secret",
        )

    @pytest.mark.asyncio
    async def test_invalid_amount_rejected(self, wave_provider):
        """Test that invalid amounts are rejected."""
        result = await wave_provider.initiate_payment(
            amount=Decimal("-50.00"),  # Negative amount
            currency="USD",
            customer_phone="+1234567890",
            reference="test_ref",
        )

        assert not result.success, "Negative amount should fail"
        assert result.error_code is not None, "Should have error code"

    @pytest.mark.asyncio
    async def test_invalid_currency_rejected(self, wave_provider):
        """Test that invalid currencies are rejected."""
        result = await wave_provider.initiate_payment(
            amount=Decimal("50.00"),
            currency="INVALID",  # Invalid currency
            customer_phone="+1234567890",
            reference="test_ref",
        )

        assert not result.success, "Invalid currency should fail"

    @pytest.mark.asyncio
    async def test_missing_phone_rejected(self, wave_provider):
        """Test that missing phone is rejected."""
        result = await wave_provider.initiate_payment(
            amount=Decimal("50.00"),
            currency="USD",
            customer_phone="",  # Empty phone
            reference="test_ref",
        )

        assert not result.success, "Empty phone should fail"

    @pytest.mark.asyncio
    async def test_nonexistent_transaction_status(self, wave_provider):
        """Test status check for non-existent transaction."""
        result = await wave_provider.check_status(
            transaction_id="nonexistent_txn_id",
        )

        # Should either fail or return not_found status
        assert not result.success or result.status == "not_found", (
            "Non-existent transaction should fail or return not_found"
        )
