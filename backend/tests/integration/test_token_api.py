"""
Integration Tests for OpenPAYGO Token API Endpoints

Tests all token-related API endpoints:
- GET /device/{device_id}/token - Simple token generation
- POST /device/{device_id}/token - Advanced token generation
- POST /device/{device_id}/token/from-transaction - Generate from transaction
- POST /device/{device_id}/validate - Validate token
- GET /device/{device_id}/tokens - List tokens

CRITICAL: Each test includes clear assertion messages.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, DeviceStatus
from app.models.device_token import DeviceToken
from app.models.transaction import Transaction, TransactionStatus


@pytest.mark.asyncio
class TestTokenGenerationEndpoint:
    """Tests for GET /device/{device_id}/token endpoint."""

    async def test_generate_token_simple(self, client, auth_headers, sample_device):
        """Test simple token generation with days_valid parameter."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 30},
            headers=auth_headers,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "token" in data, "Response must contain 'token' field"
        assert "expires_at" in data, "Response must contain 'expires_at' field"
        assert "days_valid" in data, "Response must contain 'days_valid' field"
        assert data["days_valid"] == 30, f"Expected 30 days, got {data['days_valid']}"

    async def test_generate_token_format(self, client, auth_headers, sample_device):
        """Test that generated token has correct XXX-XXX-XXX format."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 7},
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        token = data["token"]

        # Validate format
        parts = token.split("-")
        assert len(parts) == 3, f"Token should have 3 parts separated by dashes, got: {token}"
        for i, part in enumerate(parts):
            assert len(part) == 3, f"Part {i+1} should be 3 digits, got: {part}"
            assert part.isdigit(), f"Part {i+1} should be numeric, got: {part}"

    async def test_generate_token_expiry_calculation(self, client, auth_headers, sample_device):
        """Test that token expiry is calculated correctly."""
        days = 14

        before = datetime.now(timezone.utc)
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": days},
            headers=auth_headers,
        )
        after = datetime.now(timezone.utc)

        assert response.status_code == 200

        data = response.json()
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))

        # Check expiry is approximately days from now
        expected_min = before + timedelta(days=days)
        expected_max = after + timedelta(days=days)

        assert expires_at >= expected_min, "Expiry date too early"
        assert expires_at <= expected_max, "Expiry date too late"

    async def test_generate_token_device_not_found(self, client, auth_headers):
        """Test error when device doesn't exist."""
        fake_device_id = str(uuid4())

        response = await client.get(
            f"/api/v1/openpaygo/device/{fake_device_id}/token",
            params={"days_valid": 30},
            headers=auth_headers,
        )

        assert response.status_code == 404, f"Expected 404, got {response.status_code}"

        data = response.json()
        assert "error_code" in data, "Error response should contain error_code"
        assert data["error_code"] == "DEVICE_NOT_FOUND"

    async def test_generate_token_default_days(self, client, auth_headers, sample_device):
        """Test token generation with default days (from config)."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert data["days_valid"] == 30, "Default days should be 30"

    async def test_generate_token_max_days_limit(self, client, auth_headers, sample_device):
        """Test error when requesting days beyond maximum."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 9999},  # Way beyond max
            headers=auth_headers,
        )

        assert response.status_code == 400, f"Expected 400 for exceeding max days, got {response.status_code}"

        data = response.json()
        assert "error_code" in data
        assert data["error_code"] == "TOKEN_GENERATION_ERROR"

    async def test_generate_token_zero_days_fails(self, client, auth_headers, sample_device):
        """Test error when requesting 0 days."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 0},
            headers=auth_headers,
        )

        assert response.status_code == 400, "Zero days should return 400"

    async def test_generate_token_negative_days_fails(self, client, auth_headers, sample_device):
        """Test error when requesting negative days."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": -5},
            headers=auth_headers,
        )

        assert response.status_code in [400, 422], "Negative days should fail"


@pytest.mark.asyncio
class TestAdvancedTokenGenerationEndpoint:
    """Tests for POST /device/{device_id}/token endpoint."""

    async def test_advanced_generation_add_time(self, client, auth_headers, sample_device):
        """Test advanced token generation with ADD_TIME type."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            json={
                "days_valid": 15,
                "token_type": "ADD_TIME",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["token_type"] == "ADD_TIME"
        assert data["days_valid"] == 15

    async def test_advanced_generation_set_time(self, client, auth_headers, sample_device):
        """Test advanced token generation with SET_TIME type."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            json={
                "days_valid": 30,
                "token_type": "SET_TIME",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert data["token_type"] == "SET_TIME"

    async def test_advanced_generation_with_metadata(self, client, auth_headers, sample_device):
        """Test token generation with custom metadata."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            json={
                "days_valid": 30,
                "metadata": {
                    "reason": "monthly_payment",
                    "operator_id": "op_123",
                },
            },
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert "token" in data

    async def test_advanced_generation_returns_counter(self, client, auth_headers, sample_device):
        """Test that advanced generation returns counter value."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            json={"days_valid": 7},
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert "counter" in data, "Response should contain counter"
        assert isinstance(data["counter"], int), "Counter should be integer"
        assert data["counter"] >= 1, "Counter should be at least 1"


@pytest.mark.asyncio
class TestTokenFromTransactionEndpoint:
    """Tests for POST /device/{device_id}/token/from-transaction endpoint."""

    async def test_generate_from_completed_transaction(
        self, client, auth_headers, sample_device, sample_transaction, db_session
    ):
        """Test token generation from a completed transaction."""
        # First mark transaction as completed
        sample_transaction.status = TransactionStatus.COMPLETED
        sample_transaction.completed_at = datetime.now(timezone.utc)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token/from-transaction",
            json={"transaction_id": str(sample_transaction.id)},
            headers=auth_headers,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "token" in data
        assert data["transaction_id"] == str(sample_transaction.id)
        # $50 at $1/day = 50 days
        assert data["days_valid"] == 50, f"Expected 50 days for $50, got {data['days_valid']}"

    async def test_generate_from_transaction_custom_price(
        self, client, auth_headers, sample_device, sample_transaction, db_session
    ):
        """Test token generation with custom price per day."""
        sample_transaction.status = TransactionStatus.COMPLETED
        sample_transaction.completed_at = datetime.now(timezone.utc)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token/from-transaction",
            json={
                "transaction_id": str(sample_transaction.id),
                "price_per_day": "2.50",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        # $50 at $2.50/day = 20 days
        assert data["days_valid"] == 20, f"Expected 20 days for $50 at $2.50/day, got {data['days_valid']}"

    async def test_generate_from_pending_transaction_fails(
        self, client, auth_headers, sample_device, sample_transaction
    ):
        """Test error when transaction is not completed."""
        # sample_transaction is PENDING by default

        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token/from-transaction",
            json={"transaction_id": str(sample_transaction.id)},
            headers=auth_headers,
        )

        assert response.status_code == 400, "Pending transaction should return 400"

        data = response.json()
        assert data["error_code"] == "TOKEN_GENERATION_ERROR"

    async def test_generate_from_nonexistent_transaction(
        self, client, auth_headers, sample_device
    ):
        """Test error when transaction doesn't exist."""
        fake_transaction_id = str(uuid4())

        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/token/from-transaction",
            json={"transaction_id": fake_transaction_id},
            headers=auth_headers,
        )

        assert response.status_code == 404


@pytest.mark.asyncio
class TestTokenValidationEndpoint:
    """Tests for POST /device/{device_id}/validate endpoint."""

    async def test_validate_valid_token(self, client, auth_headers, sample_device):
        """Test validation of a valid token."""
        # First generate a token
        gen_response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 30},
            headers=auth_headers,
        )

        assert gen_response.status_code == 200
        token = gen_response.json()["token"]

        # Now validate it
        val_response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/validate",
            json={"token": token},
            headers=auth_headers,
        )

        assert val_response.status_code == 200, f"Expected 200, got {val_response.status_code}: {val_response.text}"

        data = val_response.json()
        assert data["valid"] is True, "Token should be valid"
        assert "days" in data, "Should return days value"

    async def test_validate_invalid_format(self, client, auth_headers, sample_device):
        """Test validation fails for invalid token format."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/validate",
            json={"token": "invalid-token"},
            headers=auth_headers,
        )

        assert response.status_code == 400, "Invalid format should return 400"

        data = response.json()
        assert data["error_code"] == "INVALID_TOKEN"

    async def test_validate_wrong_token(self, client, auth_headers, sample_device):
        """Test validation fails for wrong token (valid format, wrong value)."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/validate",
            json={"token": "123-456-789"},  # Valid format but wrong
            headers=auth_headers,
        )

        # Should either return 400 or 200 with valid=False
        assert response.status_code in [200, 400]

        data = response.json()
        if response.status_code == 200:
            assert data["valid"] is False

    async def test_validate_and_consume_token(self, client, auth_headers, sample_device):
        """Test validating and consuming a token in one operation."""
        # Generate a token
        gen_response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 7},
            headers=auth_headers,
        )

        token = gen_response.json()["token"]

        # Validate and consume
        val_response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/validate",
            json={"token": token, "consume": True},
            headers=auth_headers,
        )

        assert val_response.status_code == 200

        data = val_response.json()
        assert data["valid"] is True
        assert data.get("consumed", False) is True or "consumed" not in data


@pytest.mark.asyncio
class TestTokenListEndpoint:
    """Tests for GET /device/{device_id}/tokens endpoint."""

    async def test_list_device_tokens(self, client, auth_headers, sample_device):
        """Test listing all tokens for a device."""
        # Generate several tokens
        for _ in range(3):
            await client.get(
                f"/api/v1/openpaygo/device/{sample_device.id}/token",
                params={"days_valid": 7},
                headers=auth_headers,
            )

        # List tokens
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/tokens",
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list), "Response should be a list"
        assert len(data) >= 3, f"Should have at least 3 tokens, got {len(data)}"

    async def test_list_tokens_with_pagination(self, client, auth_headers, sample_device):
        """Test listing tokens with pagination."""
        # Generate several tokens
        for _ in range(5):
            await client.get(
                f"/api/v1/openpaygo/device/{sample_device.id}/token",
                params={"days_valid": 7},
                headers=auth_headers,
            )

        # List with limit
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/tokens",
            params={"limit": 2, "offset": 0},
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert len(data) <= 2, "Should respect limit"

    async def test_list_tokens_empty(self, client, auth_headers, sample_device):
        """Test listing tokens when none exist."""
        # List tokens for fresh device (no tokens yet)
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/tokens",
            headers=auth_headers,
        )

        # Should return empty list, not error
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
class TestTokenCounterBehavior:
    """Tests for token counter behavior across multiple generations."""

    async def test_counter_increments(self, client, auth_headers, sample_device):
        """Test that counter increments with each token generation."""
        counters = []

        # Generate multiple tokens
        for _ in range(3):
            response = await client.post(
                f"/api/v1/openpaygo/device/{sample_device.id}/token",
                json={"days_valid": 7},
                headers=auth_headers,
            )

            assert response.status_code == 200
            counters.append(response.json()["counter"])

        # Verify counters increment
        assert counters[1] > counters[0], f"Second counter {counters[1]} should be > first {counters[0]}"
        assert counters[2] > counters[1], f"Third counter {counters[2]} should be > second {counters[1]}"

    async def test_tokens_are_unique(self, client, auth_headers, sample_device):
        """Test that each generated token is unique."""
        tokens = set()

        for _ in range(5):
            response = await client.get(
                f"/api/v1/openpaygo/device/{sample_device.id}/token",
                params={"days_valid": 7},
                headers=auth_headers,
            )

            assert response.status_code == 200
            tokens.add(response.json()["token"])

        assert len(tokens) == 5, "All tokens should be unique"


@pytest.mark.asyncio
class TestAuthenticationRequired:
    """Tests for authentication requirements on token endpoints."""

    async def test_token_generation_requires_auth(self, client, sample_device):
        """Test that token generation requires authentication."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 30},
            # No auth headers
        )

        assert response.status_code == 401 or response.status_code == 403

    async def test_token_list_requires_auth(self, client, sample_device):
        """Test that token listing requires authentication."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/tokens",
            # No auth headers
        )

        assert response.status_code == 401 or response.status_code == 403

    async def test_token_validation_requires_auth(self, client, sample_device):
        """Test that token validation requires authentication."""
        response = await client.post(
            f"/api/v1/openpaygo/device/{sample_device.id}/validate",
            json={"token": "123-456-789"},
            # No auth headers
        )

        assert response.status_code == 401 or response.status_code == 403
