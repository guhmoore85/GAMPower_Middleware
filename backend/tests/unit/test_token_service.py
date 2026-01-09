"""
Unit Tests for OpenPAYGO Token Service

Tests token generation and validation using OpenPAYGO v2 algorithm.

CRITICAL: Each test asserts with clear failure messages.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import (
    DeviceNotFoundError,
    DeviceSuspendedError,
    InvalidTokenError,
    TokenExpiredError,
    TokenGenerationError,
    TransactionNotFoundError,
)
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_token import DeviceToken
from app.models.transaction import PaymentProvider, Transaction, TransactionStatus
from app.services.token_service import (
    OpenPAYGOTokenService,
    TokenGenerationResult,
    TokenType,
    TokenValidationResult,
)


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def token_service(mock_db_session):
    """Create token service with mocked DB."""
    return OpenPAYGOTokenService(mock_db_session)


@pytest.fixture
def sample_device():
    """Create a sample device for testing."""
    device = Device(
        id=uuid4(),
        external_id="test_device_001",
        device_type=DeviceType.SOLAR,
        manufacturer="TestMfg",
        model="TestModel",
        openpaygo_secret_key="encrypted_secret_key_value",
        status=DeviceStatus.ACTIVE,
    )
    return device


@pytest.fixture
def sample_transaction(sample_device):
    """Create a sample completed transaction."""
    return Transaction(
        id=uuid4(),
        customer_id=uuid4(),
        device_id=sample_device.id,
        amount=Decimal("50.00"),
        currency="USD",
        payment_provider=PaymentProvider.WAVE,
        provider_transaction_id=f"wave_{uuid4().hex[:12]}",
        status=TransactionStatus.COMPLETED,
        idempotency_key=f"test_{uuid4().hex[:8]}",
        completed_at=datetime.now(timezone.utc),
    )


class TestTokenGeneration:
    """Tests for token generation."""

    @pytest.mark.asyncio
    async def test_generate_token_success(self, token_service, mock_db_session, sample_device):
        """Test successful token generation."""
        # Mock device lookup
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        # Mock encryption/decryption
        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64  # 32 bytes hex

            result = await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
            )

        assert result is not None, "Result should not be None"
        assert isinstance(result, TokenGenerationResult), "Should return TokenGenerationResult"
        assert result.device_id == sample_device.id, "Device ID should match"
        assert result.days_added == 30, "Days should be 30"
        assert result.token_type == TokenType.ADD_TIME, "Default type should be ADD_TIME"

    @pytest.mark.asyncio
    async def test_generate_token_format(self, token_service, mock_db_session, sample_device):
        """Test that generated token has correct format (XXX-XXX-XXX)."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            result = await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
            )

        # Validate format
        assert "-" in result.token, "Token should contain dashes"
        parts = result.token.split("-")
        assert len(parts) == 3, f"Token should have 3 parts, got {len(parts)}"
        for part in parts:
            assert len(part) == 3, f"Each part should be 3 digits, got {len(part)}"
            assert part.isdigit(), f"Token parts should be numeric, got {part}"

    @pytest.mark.asyncio
    async def test_generate_token_increments_counter(
        self, token_service, mock_db_session, sample_device
    ):
        """Test that token generation increments counter."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        # Mock counter lookup - return 5 as current counter
        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 5

        mock_db_session.execute.side_effect = [
            device_result,  # Device lookup
            counter_result,  # Counter lookup
        ]

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            result = await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
            )

        assert result.counter_value == 6, "Counter should be incremented to 6"

    @pytest.mark.asyncio
    async def test_generate_token_device_not_found(self, token_service, mock_db_session):
        """Test error when device doesn't exist."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = device_result

        with pytest.raises(DeviceNotFoundError) as exc_info:
            await token_service.generate_token(
                device_id=uuid4(),
                days_valid=30,
            )

        assert "not found" in str(exc_info.value).lower(), (
            "Error should mention device not found"
        )

    @pytest.mark.asyncio
    async def test_generate_token_device_suspended(
        self, token_service, mock_db_session, sample_device
    ):
        """Test error when device is suspended."""
        sample_device.status = DeviceStatus.SUSPENDED

        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        with pytest.raises(DeviceSuspendedError) as exc_info:
            await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
            )

        assert "suspended" in str(exc_info.value).lower(), (
            "Error should mention device suspended"
        )

    @pytest.mark.asyncio
    async def test_generate_token_invalid_days(self, token_service, mock_db_session, sample_device):
        """Test error with invalid days_valid."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        # Test zero days
        with pytest.raises(TokenGenerationError) as exc_info:
            await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=0,
            )

        assert "positive" in str(exc_info.value).lower(), (
            "Error should mention days must be positive"
        )

    @pytest.mark.asyncio
    async def test_generate_token_exceeds_max_days(
        self, token_service, mock_db_session, sample_device
    ):
        """Test error when days exceeds maximum."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        with pytest.raises(TokenGenerationError) as exc_info:
            await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=9999,  # Exceeds max
            )

        assert "exceeds" in str(exc_info.value).lower() or "maximum" in str(exc_info.value).lower(), (
            "Error should mention exceeds maximum"
        )


class TestTokenGenerationFromTransaction:
    """Tests for token generation from transaction."""

    @pytest.mark.asyncio
    async def test_generate_from_transaction_success(
        self, token_service, mock_db_session, sample_device, sample_transaction
    ):
        """Test successful token generation from transaction."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        transaction_result = MagicMock()
        transaction_result.scalar_one_or_none.return_value = sample_transaction

        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 0

        mock_db_session.execute.side_effect = [
            device_result,
            transaction_result,
            device_result,  # Re-fetch device
            counter_result,
        ]

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            result = await token_service.generate_token_from_transaction(
                device_id=sample_device.id,
                transaction_id=sample_transaction.id,
            )

        assert result is not None, "Result should not be None"
        assert result.transaction_id == sample_transaction.id, "Transaction ID should match"
        # $50 with default $1/day = 50 days
        assert result.days_added == 50, f"Days should be 50, got {result.days_added}"

    @pytest.mark.asyncio
    async def test_generate_from_transaction_custom_price(
        self, token_service, mock_db_session, sample_device, sample_transaction
    ):
        """Test token generation with custom price per day."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        transaction_result = MagicMock()
        transaction_result.scalar_one_or_none.return_value = sample_transaction

        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 0

        mock_db_session.execute.side_effect = [
            device_result,
            transaction_result,
            device_result,
            counter_result,
        ]

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            result = await token_service.generate_token_from_transaction(
                device_id=sample_device.id,
                transaction_id=sample_transaction.id,
                price_per_day=Decimal("5.00"),  # $5/day
            )

        # $50 with $5/day = 10 days
        assert result.days_added == 10, f"Days should be 10, got {result.days_added}"

    @pytest.mark.asyncio
    async def test_generate_from_transaction_not_completed(
        self, token_service, mock_db_session, sample_device, sample_transaction
    ):
        """Test error when transaction is not completed."""
        sample_transaction.status = TransactionStatus.PENDING

        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        transaction_result = MagicMock()
        transaction_result.scalar_one_or_none.return_value = sample_transaction

        mock_db_session.execute.side_effect = [device_result, transaction_result]

        with pytest.raises(TokenGenerationError) as exc_info:
            await token_service.generate_token_from_transaction(
                device_id=sample_device.id,
                transaction_id=sample_transaction.id,
            )

        assert "completed" in str(exc_info.value).lower(), (
            "Error should mention transaction must be completed"
        )

    @pytest.mark.asyncio
    async def test_generate_from_transaction_not_found(
        self, token_service, mock_db_session, sample_device
    ):
        """Test error when transaction not found."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        transaction_result = MagicMock()
        transaction_result.scalar_one_or_none.return_value = None

        mock_db_session.execute.side_effect = [device_result, transaction_result]

        with pytest.raises(TransactionNotFoundError) as exc_info:
            await token_service.generate_token_from_transaction(
                device_id=sample_device.id,
                transaction_id=uuid4(),
            )

        assert "not found" in str(exc_info.value).lower()


class TestTokenValidation:
    """Tests for token validation."""

    @pytest.mark.asyncio
    async def test_validate_token_format_valid(self, token_service):
        """Test valid token format."""
        assert token_service._validate_token_format("123-456-789") is True
        assert token_service._validate_token_format("000-000-000") is True
        assert token_service._validate_token_format("999-999-999") is True

    @pytest.mark.asyncio
    async def test_validate_token_format_invalid(self, token_service):
        """Test invalid token formats."""
        assert token_service._validate_token_format("") is False
        assert token_service._validate_token_format("123456789") is False
        assert token_service._validate_token_format("12-345-6789") is False
        assert token_service._validate_token_format("abc-def-ghi") is False
        assert token_service._validate_token_format("123-456-78") is False

    @pytest.mark.asyncio
    async def test_validate_token_invalid_format_error(
        self, token_service, mock_db_session, sample_device
    ):
        """Test validation error for invalid format."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device
        mock_db_session.execute.return_value = device_result

        with pytest.raises(InvalidTokenError) as exc_info:
            await token_service.validate_token(
                device_id=sample_device.id,
                token="invalid",
            )

        assert "format" in str(exc_info.value).lower()


class TestTokenTypes:
    """Tests for different token types."""

    @pytest.mark.asyncio
    async def test_generate_add_time_token(
        self, token_service, mock_db_session, sample_device
    ):
        """Test ADD_TIME token generation."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 0

        mock_db_session.execute.side_effect = [device_result, counter_result]

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            result = await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
                token_type=TokenType.ADD_TIME,
            )

        assert result.token_type == TokenType.ADD_TIME

    @pytest.mark.asyncio
    async def test_generate_set_time_token(
        self, token_service, mock_db_session, sample_device
    ):
        """Test SET_TIME token generation."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 0

        mock_db_session.execute.side_effect = [device_result, counter_result]

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            result = await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
                token_type=TokenType.SET_TIME,
            )

        assert result.token_type == TokenType.SET_TIME


class TestDaysCalculation:
    """Tests for days calculation from amount."""

    def test_calculate_days_default_price(self, token_service):
        """Test days calculation with default $1/day."""
        days = token_service._calculate_days_from_amount(
            Decimal("50.00"),
            Decimal("1.00"),
        )
        assert days == 50, f"Expected 50 days, got {days}"

    def test_calculate_days_custom_price(self, token_service):
        """Test days calculation with custom price."""
        days = token_service._calculate_days_from_amount(
            Decimal("100.00"),
            Decimal("2.00"),
        )
        assert days == 50, f"Expected 50 days, got {days}"

    def test_calculate_days_minimum_one(self, token_service):
        """Test minimum 1 day is returned."""
        days = token_service._calculate_days_from_amount(
            Decimal("0.50"),
            Decimal("1.00"),
        )
        assert days == 1, "Should return minimum 1 day"

    def test_calculate_days_rounds_down(self, token_service):
        """Test days rounds down for partial days."""
        days = token_service._calculate_days_from_amount(
            Decimal("15.50"),
            Decimal("1.00"),
        )
        assert days == 15, "Should round down to 15"


class TestTokenExpiry:
    """Tests for token expiry handling."""

    @pytest.mark.asyncio
    async def test_token_expiry_calculated_correctly(
        self, token_service, mock_db_session, sample_device
    ):
        """Test that token expiry is calculated correctly."""
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = sample_device

        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 0

        mock_db_session.execute.side_effect = [device_result, counter_result]

        with patch("app.services.token_service.decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "a" * 64

            before = datetime.now(timezone.utc)
            result = await token_service.generate_token(
                device_id=sample_device.id,
                days_valid=30,
            )
            after = datetime.now(timezone.utc)

        # Expiry should be approximately 30 days from now
        expected_min = before + timedelta(days=30)
        expected_max = after + timedelta(days=30)

        assert result.expires_at >= expected_min, "Expiry too early"
        assert result.expires_at <= expected_max, "Expiry too late"


class TestCounterManagement:
    """Tests for OpenPAYGO counter management."""

    @pytest.mark.asyncio
    async def test_get_device_counter_no_tokens(
        self, token_service, mock_db_session
    ):
        """Test counter returns 0 when no tokens exist."""
        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = counter_result

        counter = await token_service.get_device_counter(uuid4())

        assert counter == 0, "Counter should be 0 for new device"

    @pytest.mark.asyncio
    async def test_get_device_counter_with_tokens(
        self, token_service, mock_db_session
    ):
        """Test counter returns highest value."""
        counter_result = MagicMock()
        counter_result.scalar_one_or_none.return_value = 42
        mock_db_session.execute.return_value = counter_result

        counter = await token_service.get_device_counter(uuid4())

        assert counter == 42, "Counter should be 42"


class TestCryptographicOperations:
    """Tests for cryptographic token operations."""

    def test_compute_token_v2_produces_valid_output(self, token_service):
        """Test that token computation produces valid 9-digit output."""
        secret_key = bytes.fromhex("a" * 64)
        token = token_service._compute_token_v2(
            secret_key=secret_key,
            counter=1,
            token_type=TokenType.ADD_TIME,
            value=30,
        )

        assert 100000000 <= token <= 999999999, (
            f"Token should be 9 digits, got {token}"
        )

    def test_compute_token_v2_deterministic(self, token_service):
        """Test that same inputs produce same token."""
        secret_key = bytes.fromhex("a" * 64)

        token1 = token_service._compute_token_v2(
            secret_key=secret_key,
            counter=1,
            token_type=TokenType.ADD_TIME,
            value=30,
        )

        token2 = token_service._compute_token_v2(
            secret_key=secret_key,
            counter=1,
            token_type=TokenType.ADD_TIME,
            value=30,
        )

        assert token1 == token2, "Same inputs should produce same token"

    def test_compute_token_v2_different_counter(self, token_service):
        """Test that different counters produce different tokens."""
        secret_key = bytes.fromhex("a" * 64)

        token1 = token_service._compute_token_v2(
            secret_key=secret_key,
            counter=1,
            token_type=TokenType.ADD_TIME,
            value=30,
        )

        token2 = token_service._compute_token_v2(
            secret_key=secret_key,
            counter=2,
            token_type=TokenType.ADD_TIME,
            value=30,
        )

        assert token1 != token2, "Different counters should produce different tokens"

    def test_format_token(self, token_service):
        """Test token formatting."""
        formatted = token_service._format_token(123456789)
        assert formatted == "123-456-789", f"Expected '123-456-789', got '{formatted}'"

        formatted = token_service._format_token(100000000)
        assert formatted == "100-000-000", f"Expected '100-000-000', got '{formatted}'"
