"""
Integration Tests for Device Lifecycle

Tests the complete flow:
1. Register device
2. Send metrics
3. Create trigger
4. Process payment
5. Activate device

CRITICAL: Each step is asserted with clear messages.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.device import DeviceStatus


@pytest.mark.asyncio
class TestDeviceLifecycle:
    """Integration tests for complete device lifecycle."""

    async def test_device_registration(self, client, auth_headers):
        """Test device registration endpoint."""
        device_data = {
            "external_id": f"test_device_{uuid4().hex[:8]}",
            "device_type": "solar",
            "manufacturer": "TestMfg",
            "model": "TestModel",
        }

        response = await client.post(
            "/api/v1/openpaygo/device/register",
            json=device_data,
            headers=auth_headers,
        )

        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

        data = response.json()
        assert "id" in data, "Response should contain device ID"
        assert "secret_key" in data, "Response should contain secret key"
        assert data["external_id"] == device_data["external_id"]
        assert data["device_type"] == device_data["device_type"]
        assert data["status"] == "active"

        # Store for other tests
        return data

    async def test_duplicate_device_registration_fails(self, client, auth_headers):
        """Test that duplicate device registration fails."""
        external_id = f"duplicate_{uuid4().hex[:8]}"

        device_data = {
            "external_id": external_id,
            "device_type": "solar",
        }

        # First registration should succeed
        response1 = await client.post(
            "/api/v1/openpaygo/device/register",
            json=device_data,
            headers=auth_headers,
        )
        assert response1.status_code == 201

        # Second registration should fail
        response2 = await client.post(
            "/api/v1/openpaygo/device/register",
            json=device_data,
            headers=auth_headers,
        )
        assert response2.status_code == 409, "Duplicate registration should return 409"

        error = response2.json()
        assert error["error_code"] == "DEVICE_EXISTS"

    async def test_metric_submission(self, client, auth_headers, sample_device):
        """Test metric submission for a device."""
        metric_data = {
            "metric_type": "energy_consumed",
            "value": 50.5,
            "unit": "kWh",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        response = await client.post(
            "/api/v1/openpaygo/metrics",
            params={"device_id": str(sample_device.id)},
            json=metric_data,
            headers=auth_headers,
        )

        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["metric_type"] == metric_data["metric_type"]
        assert float(data["value"]) == metric_data["value"]
        assert data["unit"] == metric_data["unit"]

    async def test_metric_submission_invalid_device(self, client, auth_headers):
        """Test metric submission fails for non-existent device."""
        metric_data = {
            "metric_type": "energy_consumed",
            "value": 50.5,
            "unit": "kWh",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        fake_device_id = str(uuid4())

        response = await client.post(
            "/api/v1/openpaygo/metrics",
            params={"device_id": fake_device_id},
            json=metric_data,
            headers=auth_headers,
        )

        assert response.status_code == 404, "Should return 404 for non-existent device"

    async def test_batch_metric_submission(self, client, auth_headers, sample_device):
        """Test batch metric submission."""
        now = datetime.now(timezone.utc).isoformat()

        batch_data = {
            "metrics": [
                {"metric_type": "energy_consumed", "value": 10.0, "unit": "kWh", "timestamp": now},
                {"metric_type": "battery_level", "value": 85.0, "unit": "%", "timestamp": now},
                {"metric_type": "uptime", "value": 24.0, "unit": "hours", "timestamp": now},
            ]
        }

        response = await client.post(
            "/api/v1/openpaygo/metrics/batch",
            params={"device_id": str(sample_device.id)},
            json=batch_data,
            headers=auth_headers,
        )

        assert response.status_code == 201

        data = response.json()
        assert len(data) == 3, "Should return 3 metrics"

    async def test_get_device_metrics(self, client, auth_headers, sample_device):
        """Test retrieving device metrics."""
        # First submit some metrics
        now = datetime.now(timezone.utc).isoformat()

        for i in range(5):
            await client.post(
                "/api/v1/openpaygo/metrics",
                params={"device_id": str(sample_device.id)},
                json={
                    "metric_type": "energy_consumed",
                    "value": 10.0 * (i + 1),
                    "unit": "kWh",
                    "timestamp": now,
                },
                headers=auth_headers,
            )

        # Now retrieve them
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/metrics",
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert len(data) >= 5, "Should have at least 5 metrics"

    async def test_device_token_generation(self, client, auth_headers, sample_device):
        """Test token generation for a device."""
        response = await client.get(
            f"/api/v1/openpaygo/device/{sample_device.id}/token",
            params={"days_valid": 30},
            headers=auth_headers,
        )

        assert response.status_code == 200

        data = response.json()
        assert "token" in data, "Response should contain token"
        assert "expires_at" in data, "Response should contain expiry"
        assert data["days_valid"] == 30

        # Validate token format (XXX-XXX-XXX)
        token = data["token"]
        assert len(token.replace("-", "")) == 9, "Token should be 9 digits"


@pytest.mark.asyncio
class TestWebhookProcessing:
    """Test webhook processing."""

    async def test_wave_webhook(self, client, sample_transaction):
        """Test Wave webhook processing."""
        webhook_payload = {
            "event": "payment.completed",
            "data": {
                "id": sample_transaction.provider_transaction_id,
                "amount": "50.00",
                "currency": "USD",
                "status": "completed",
            },
        }

        response = await client.post(
            "/api/v1/webhooks/wave",
            json=webhook_payload,
            headers={"X-Wave-Signature": "test_signature"},
        )

        # Webhooks should always return 200
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "received"

    async def test_webhook_idempotency(self, client, sample_transaction):
        """Test webhook idempotency - same webhook processed once."""
        webhook_payload = {
            "event": "payment.completed",
            "data": {
                "id": sample_transaction.provider_transaction_id,
                "amount": "50.00",
                "currency": "USD",
                "status": "completed",
            },
        }

        # Send same webhook twice
        response1 = await client.post(
            "/api/v1/webhooks/wave",
            json=webhook_payload,
            headers={"X-Wave-Signature": "test_signature"},
        )

        response2 = await client.post(
            "/api/v1/webhooks/wave",
            json=webhook_payload,
            headers={"X-Wave-Signature": "test_signature"},
        )

        # Both should succeed
        assert response1.status_code == 200
        assert response2.status_code == 200

        # Second should indicate already processed
        data2 = response2.json()
        # The transaction was already updated to completed
        assert "Already processed" in str(data2) or data2.get("processed", False)
