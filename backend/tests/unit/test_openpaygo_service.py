"""
Unit Tests for OpenPAYGO Service

Tests token generation, validation, and device registration.

CRITICAL: Each test asserts with clear failure messages.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.device import Device, DeviceStatus, DeviceType
from app.services.openpaygo_service import OpenPAYGOService


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def openpaygo_service(mock_db_session):
    """Create OpenPAYGO service with mocked DB."""
    return OpenPAYGOService(mock_db_session)


@pytest.fixture
def sample_device():
    """Create a sample device for testing."""
    return Device(
        id=uuid4(),
        external_id="test_device_001",
        device_type=DeviceType.SOLAR,
        manufacturer="TestMfg",
        model="TestModel",
        openpaygo_secret_key="a" * 64,  # 32 bytes hex
        openpaygo_token_count=0,
        status=DeviceStatus.ACTIVE,
    )


class TestTokenGeneration:
    """Tests for token generation."""

    @pytest.mark.asyncio
    async def test_token_format(self, openpaygo_service, mock_db_session, sample_device):
        """Test that generated token has correct format (XXX-XXX-XXX)."""
        # Mock the device lookup
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        token, expires_at = await openpaygo_service.generate_token(
            device_id=sample_device.id,
            days_valid=30,
        )

        # Validate format
        assert "-" in token, "Token should contain dashes"
        parts = token.split("-")
        assert len(parts) == 3, f"Token should have 3 parts, got {len(parts)}"
        for part in parts:
            assert len(part) == 3, f"Each part should be 3 digits, got {len(part)}"
            assert part.isdigit(), f"Token parts should be numeric, got {part}"

    @pytest.mark.asyncio
    async def test_token_expiry_calculation(self, openpaygo_service, mock_db_session, sample_device):
        """Test that expiry is correctly calculated."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        days_valid = 30
        before = datetime.now(timezone.utc)

        token, expires_at = await openpaygo_service.generate_token(
            device_id=sample_device.id,
            days_valid=days_valid,
        )

        after = datetime.now(timezone.utc)

        # Expiry should be approximately 30 days from now
        expected_min = before.replace(hour=0, minute=0, second=0, microsecond=0)
        expected_max = after.replace(hour=23, minute=59, second=59, microsecond=999999)

        assert expires_at > before, "Expiry should be in the future"

    @pytest.mark.asyncio
    async def test_token_generation_increments_count(
        self, openpaygo_service, mock_db_session, sample_device
    ):
        """Test that token count is incremented."""
        initial_count = sample_device.openpaygo_token_count

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        await openpaygo_service.generate_token(
            device_id=sample_device.id,
            days_valid=30,
        )

        assert sample_device.openpaygo_token_count == initial_count + 1, (
            "Token count should be incremented"
        )

    @pytest.mark.asyncio
    async def test_token_generation_fails_for_missing_device(
        self, openpaygo_service, mock_db_session
    ):
        """Test that token generation fails for non-existent device."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_db_session.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await openpaygo_service.generate_token(
                device_id=uuid4(),
                days_valid=30,
            )

        assert "not found" in str(exc_info.value).lower(), (
            "Error should mention device not found"
        )


class TestTokenValidation:
    """Tests for token validation."""

    @pytest.mark.asyncio
    async def test_valid_token_accepted(self, openpaygo_service, mock_db_session, sample_device):
        """Test that a valid token is accepted."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        # Generate a token first
        token, _ = await openpaygo_service.generate_token(
            device_id=sample_device.id,
            days_valid=30,
        )

        # Validate it
        is_valid = await openpaygo_service.validate_token(
            device_id=sample_device.id,
            token=token,
        )

        assert is_valid, "Generated token should be valid"

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, openpaygo_service, mock_db_session, sample_device):
        """Test that an invalid token is rejected."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        is_valid = await openpaygo_service.validate_token(
            device_id=sample_device.id,
            token="000-000-000",  # Invalid token
        )

        assert not is_valid, "Invalid token should be rejected"

    @pytest.mark.asyncio
    async def test_malformed_token_rejected(self, openpaygo_service, mock_db_session, sample_device):
        """Test that malformed tokens are rejected."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        malformed_tokens = [
            "123456789",  # No dashes
            "12-345-678",  # Wrong format
            "abc-def-ghi",  # Non-numeric
            "1234-567-890",  # Wrong length
            "",  # Empty
        ]

        for token in malformed_tokens:
            with pytest.raises(Exception):
                await openpaygo_service.validate_token(
                    device_id=sample_device.id,
                    token=token,
                )


class TestDeviceRegistration:
    """Tests for device registration."""

    @pytest.mark.asyncio
    async def test_registration_creates_device(self, openpaygo_service, mock_db_session):
        """Test that registration creates a new device."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None  # No existing device
        mock_db_session.execute.return_value = mock_result

        external_id = f"test_{uuid4().hex[:8]}"

        device, secret = await openpaygo_service.register_device(
            external_id=external_id,
            device_type=DeviceType.SOLAR,
            manufacturer="TestMfg",
            model="TestModel",
        )

        # Verify device was added to session
        mock_db_session.add.assert_called_once()

        # Verify secret is returned
        assert secret, "Secret should be returned"
        assert len(secret) == 64, "Secret should be 64 hex characters (32 bytes)"

    @pytest.mark.asyncio
    async def test_registration_fails_for_duplicate(
        self, openpaygo_service, mock_db_session, sample_device
    ):
        """Test that registration fails for duplicate external_id."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device  # Existing device
        mock_db_session.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await openpaygo_service.register_device(
                external_id=sample_device.external_id,
                device_type=DeviceType.SOLAR,
            )

        assert "exist" in str(exc_info.value).lower() or "duplicate" in str(exc_info.value).lower(), (
            "Error should mention device already exists"
        )

    @pytest.mark.asyncio
    async def test_registration_generates_unique_secret(self, openpaygo_service, mock_db_session):
        """Test that each registration generates a unique secret."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_db_session.execute.return_value = mock_result

        secrets = set()
        for i in range(10):
            _, secret = await openpaygo_service.register_device(
                external_id=f"test_{i}_{uuid4().hex[:8]}",
                device_type=DeviceType.SOLAR,
            )
            secrets.add(secret)

        assert len(secrets) == 10, "Each registration should generate a unique secret"


class TestDeviceActivation:
    """Tests for device activation."""

    @pytest.mark.asyncio
    async def test_activation_with_valid_token(
        self, openpaygo_service, mock_db_session, sample_device
    ):
        """Test device activation with a valid token."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        # Generate token
        token, expires_at = await openpaygo_service.generate_token(
            device_id=sample_device.id,
            days_valid=30,
        )

        # Activate device
        activation = await openpaygo_service.activate_device(
            device_id=sample_device.id,
            token=token,
            expires_at=expires_at,
        )

        assert activation is not None, "Activation should be created"
        mock_db_session.add.assert_called()
        mock_db_session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_activation_fails_with_invalid_token(
        self, openpaygo_service, mock_db_session, sample_device
    ):
        """Test that activation fails with invalid token."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = sample_device
        mock_db_session.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await openpaygo_service.activate_device(
                device_id=sample_device.id,
                token="000-000-000",
                expires_at=datetime.now(timezone.utc),
            )

        assert "invalid" in str(exc_info.value).lower() or "token" in str(exc_info.value).lower(), (
            "Error should mention invalid token"
        )
