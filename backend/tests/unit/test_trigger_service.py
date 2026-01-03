"""
Unit Tests for Trigger Service

Tests trigger evaluation for usage-based, time-based, and manual triggers.

CRITICAL: Each test asserts with clear failure messages.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.device import Device, DeviceStatus, DeviceType
from app.models.payment_trigger import PaymentTrigger, TriggerStatus, TriggerType
from app.services.trigger_service import TriggerEvaluationResult, TriggerService


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
def trigger_service(mock_db_session):
    """Create TriggerService with mocked DB."""
    return TriggerService(mock_db_session)


@pytest.fixture
def sample_device():
    """Create a sample device."""
    return Device(
        id=uuid4(),
        external_id="test_device_001",
        device_type=DeviceType.SOLAR,
        status=DeviceStatus.ACTIVE,
    )


@pytest.fixture
def usage_trigger(sample_device):
    """Create a usage-based trigger."""
    return PaymentTrigger(
        id=uuid4(),
        device_id=sample_device.id,
        trigger_type=TriggerType.USAGE_BASED,
        status=TriggerStatus.ACTIVE,
        configuration={
            "metric_type": "energy_consumed",
            "threshold": 100.0,
            "amount_per_unit": "0.10",
            "currency": "USD",
        },
        last_triggered_at=None,
    )


@pytest.fixture
def time_trigger(sample_device):
    """Create a time-based trigger."""
    return PaymentTrigger(
        id=uuid4(),
        device_id=sample_device.id,
        trigger_type=TriggerType.TIME_BASED,
        status=TriggerStatus.ACTIVE,
        configuration={
            "interval_days": 30,
            "amount": "50.00",
            "currency": "USD",
        },
        last_triggered_at=datetime.now(timezone.utc) - timedelta(days=35),
    )


@pytest.fixture
def manual_trigger(sample_device):
    """Create a manual trigger."""
    return PaymentTrigger(
        id=uuid4(),
        device_id=sample_device.id,
        trigger_type=TriggerType.MANUAL,
        status=TriggerStatus.ACTIVE,
        configuration={
            "amount": "25.00",
            "currency": "USD",
            "description": "Manual top-up",
        },
    )


class TestUsageBasedTriggers:
    """Tests for usage-based trigger evaluation."""

    @pytest.mark.asyncio
    async def test_trigger_fires_when_threshold_exceeded(
        self, trigger_service, mock_db_session, sample_device, usage_trigger
    ):
        """Test that trigger fires when usage exceeds threshold."""
        # Mock device lookup
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        # Mock trigger lookup
        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [usage_trigger]

        # Mock metrics aggregation (total usage = 150, threshold = 100)
        metrics_result = MagicMock()
        metrics_result.scalar.return_value = Decimal("150.0")

        mock_db_session.execute.side_effect = [
            device_result,
            trigger_result,
            metrics_result,
        ]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED],
        )

        assert len(results) == 1, "Should return one evaluation result"
        assert results[0].should_trigger, "Trigger should fire when threshold exceeded"
        assert results[0].amount == Decimal("15.00"), (
            "Amount should be (150-100) * 0.10 = 5.00 for exceeded units"
        )

    @pytest.mark.asyncio
    async def test_trigger_does_not_fire_below_threshold(
        self, trigger_service, mock_db_session, sample_device, usage_trigger
    ):
        """Test that trigger doesn't fire when usage is below threshold."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [usage_trigger]

        # Usage below threshold
        metrics_result = MagicMock()
        metrics_result.scalar.return_value = Decimal("50.0")

        mock_db_session.execute.side_effect = [
            device_result,
            trigger_result,
            metrics_result,
        ]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED],
        )

        assert len(results) == 1, "Should return one evaluation result"
        assert not results[0].should_trigger, "Trigger should not fire below threshold"

    @pytest.mark.asyncio
    async def test_usage_trigger_calculation(
        self, trigger_service, mock_db_session, sample_device, usage_trigger
    ):
        """Test correct calculation of usage-based amounts."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [usage_trigger]

        # 250 units consumed, threshold 100, rate 0.10
        # Billable = 250 - 100 = 150 units
        # Amount = 150 * 0.10 = 15.00
        metrics_result = MagicMock()
        metrics_result.scalar.return_value = Decimal("250.0")

        mock_db_session.execute.side_effect = [
            device_result,
            trigger_result,
            metrics_result,
        ]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED],
        )

        assert results[0].amount == Decimal("15.00"), (
            f"Expected 15.00, got {results[0].amount}"
        )


class TestTimeBasedTriggers:
    """Tests for time-based trigger evaluation."""

    @pytest.mark.asyncio
    async def test_trigger_fires_when_interval_elapsed(
        self, trigger_service, mock_db_session, sample_device, time_trigger
    ):
        """Test that trigger fires when interval has elapsed."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [time_trigger]

        mock_db_session.execute.side_effect = [device_result, trigger_result]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.TIME_BASED],
        )

        assert len(results) == 1, "Should return one evaluation result"
        assert results[0].should_trigger, (
            "Trigger should fire when interval elapsed (35 days > 30 days)"
        )
        assert results[0].amount == Decimal("50.00"), (
            f"Expected amount 50.00, got {results[0].amount}"
        )

    @pytest.mark.asyncio
    async def test_trigger_does_not_fire_within_interval(
        self, trigger_service, mock_db_session, sample_device, time_trigger
    ):
        """Test that trigger doesn't fire within interval."""
        # Set last triggered to 10 days ago (within 30 day interval)
        time_trigger.last_triggered_at = datetime.now(timezone.utc) - timedelta(days=10)

        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [time_trigger]

        mock_db_session.execute.side_effect = [device_result, trigger_result]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.TIME_BASED],
        )

        assert len(results) == 1, "Should return one evaluation result"
        assert not results[0].should_trigger, (
            "Trigger should not fire within interval (10 days < 30 days)"
        )

    @pytest.mark.asyncio
    async def test_new_trigger_fires_immediately(
        self, trigger_service, mock_db_session, sample_device, time_trigger
    ):
        """Test that new triggers (never triggered) fire immediately."""
        time_trigger.last_triggered_at = None  # Never triggered

        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [time_trigger]

        mock_db_session.execute.side_effect = [device_result, trigger_result]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.TIME_BASED],
        )

        assert results[0].should_trigger, (
            "New trigger should fire immediately"
        )


class TestManualTriggers:
    """Tests for manual trigger evaluation."""

    @pytest.mark.asyncio
    async def test_manual_trigger_returns_amount(
        self, trigger_service, mock_db_session, sample_device, manual_trigger
    ):
        """Test that manual trigger returns configured amount."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [manual_trigger]

        mock_db_session.execute.side_effect = [device_result, trigger_result]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.MANUAL],
        )

        assert len(results) == 1, "Should return one evaluation result"
        assert results[0].should_trigger, "Manual trigger should always fire"
        assert results[0].amount == Decimal("25.00"), (
            f"Expected amount 25.00, got {results[0].amount}"
        )


class TestTriggerFiltering:
    """Tests for trigger filtering and status handling."""

    @pytest.mark.asyncio
    async def test_inactive_triggers_skipped(
        self, trigger_service, mock_db_session, sample_device, usage_trigger
    ):
        """Test that inactive triggers are not evaluated."""
        usage_trigger.status = TriggerStatus.PAUSED

        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        # Active triggers query returns empty (only active triggers returned)
        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = []

        mock_db_session.execute.side_effect = [device_result, trigger_result]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED],
        )

        assert len(results) == 0, "Inactive triggers should not be evaluated"

    @pytest.mark.asyncio
    async def test_multiple_triggers_evaluated(
        self, trigger_service, mock_db_session, sample_device, usage_trigger, time_trigger
    ):
        """Test that multiple triggers are all evaluated."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [usage_trigger, time_trigger]

        metrics_result = MagicMock()
        metrics_result.scalar.return_value = Decimal("150.0")

        mock_db_session.execute.side_effect = [
            device_result,
            trigger_result,
            metrics_result,  # For usage trigger
        ]

        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED, TriggerType.TIME_BASED],
        )

        assert len(results) == 2, "Both triggers should be evaluated"


class TestDryRun:
    """Tests for dry run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_update_database(
        self, trigger_service, mock_db_session, sample_device, usage_trigger
    ):
        """Test that dry run doesn't create transactions or update triggers."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [usage_trigger]

        metrics_result = MagicMock()
        metrics_result.scalar.return_value = Decimal("150.0")

        mock_db_session.execute.side_effect = [
            device_result,
            trigger_result,
            metrics_result,
        ]

        await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED],
            dry_run=True,
        )

        # In dry run, commit should not be called
        mock_db_session.commit.assert_not_called()


class TestErrorHandling:
    """Tests for error handling in trigger evaluation."""

    @pytest.mark.asyncio
    async def test_device_not_found_error(self, trigger_service, mock_db_session):
        """Test error when device doesn't exist."""
        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = None
        mock_db_session.execute.return_value = device_result

        with pytest.raises(Exception) as exc_info:
            await trigger_service.evaluate_triggers_for_device(
                device_id=uuid4(),
                trigger_types=[TriggerType.USAGE_BASED],
            )

        assert "not found" in str(exc_info.value).lower(), (
            "Error should mention device not found"
        )

    @pytest.mark.asyncio
    async def test_invalid_trigger_configuration_logged(
        self, trigger_service, mock_db_session, sample_device, usage_trigger
    ):
        """Test that invalid trigger configuration is handled gracefully."""
        # Invalid configuration - missing required fields
        usage_trigger.configuration = {"invalid": "config"}

        device_result = MagicMock()
        device_result.scalars.return_value.first.return_value = sample_device

        trigger_result = MagicMock()
        trigger_result.scalars.return_value.all.return_value = [usage_trigger]

        mock_db_session.execute.side_effect = [device_result, trigger_result]

        # Should handle gracefully (either skip or return error result)
        results = await trigger_service.evaluate_triggers_for_device(
            device_id=sample_device.id,
            trigger_types=[TriggerType.USAGE_BASED],
        )

        # Either returns empty or returns with error flag
        if results:
            assert not results[0].should_trigger or results[0].error is not None, (
                "Invalid config should not trigger or should have error"
            )
