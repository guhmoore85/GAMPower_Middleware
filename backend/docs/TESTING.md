# Testing Guide

This document covers testing practices for the PAYGO Middleware.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Unit tests
│   ├── test_encryption.py
│   ├── test_openpaygo_service.py
│   ├── test_trigger_service.py
│   └── test_payment_providers.py
└── integration/             # Integration tests
    └── test_device_lifecycle.py
```

## Running Tests

### Quick Start

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_encryption.py

# Run specific test
pytest tests/unit/test_encryption.py::TestEncryption::test_encrypt_decrypt_roundtrip

# Run tests matching pattern
pytest -k "encryption"
```

### With Coverage

```bash
# Generate coverage report
pytest --cov=app --cov-report=html

# View report
open htmlcov/index.html
```

### Using Docker

```bash
# Run tests in container
docker-compose run --rm app pytest

# Run specific tests
docker-compose run --rm app pytest tests/unit/ -v
```

## Test Categories

### Unit Tests

Test individual components in isolation:

```python
# tests/unit/test_encryption.py
class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        """Test that encryption and decryption are inverse operations."""
        plaintext = "sensitive_data_123"

        encrypted = encrypt_value(plaintext)
        decrypted = decrypt_value(encrypted)

        assert decrypted == plaintext, "Decrypted value should match original"
```

**Characteristics:**
- No external dependencies (database, Redis)
- Fast execution
- Mocked dependencies
- Test single function/class

### Integration Tests

Test component interactions:

```python
# tests/integration/test_device_lifecycle.py
@pytest.mark.asyncio
class TestDeviceLifecycle:
    async def test_device_registration(self, client, auth_headers):
        """Test complete device registration flow."""
        response = await client.post(
            "/api/v1/openpaygo/device/register",
            json={"external_id": "TEST_001", "device_type": "solar"},
            headers=auth_headers,
        )

        assert response.status_code == 201
        assert "secret_key" in response.json()
```

**Characteristics:**
- Uses test database
- Tests API endpoints
- Verifies database state
- Tests component integration

## Fixtures

### Database Session

```python
# conftest.py
@pytest_asyncio.fixture
async def db_session():
    """Create isolated database session for testing."""
    async with async_session() as session:
        yield session
        await session.rollback()
```

### HTTP Client

```python
@pytest_asyncio.fixture
async def client():
    """Create test HTTP client."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
```

### Sample Data

```python
@pytest_asyncio.fixture
async def sample_device(db_session):
    """Create a sample device for testing."""
    device = Device(
        external_id=f"test_{uuid4().hex[:8]}",
        device_type=DeviceType.SOLAR,
        openpaygo_secret_key="a" * 64,
        status=DeviceStatus.ACTIVE,
    )
    db_session.add(device)
    await db_session.commit()
    await db_session.refresh(device)
    return device
```

### Authentication

```python
@pytest.fixture
def auth_headers():
    """Create authentication headers."""
    return {"X-API-Key": "test_api_key"}
```

## Mocking

### External Services

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_payment_initiation():
    with patch("app.services.payment.wave_provider.httpx.AsyncClient") as mock:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": "txn_123", "status": "pending"}
        mock.return_value.__aenter__.return_value.post.return_value = mock_response

        provider = WavePaymentProvider(...)
        result = await provider.initiate_payment(...)

        assert result.success
```

### Database Queries

```python
@pytest.mark.asyncio
async def test_device_lookup():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = sample_device
    mock_session.execute.return_value = mock_result

    service = OpenPAYGOService(mock_session)
    device = await service.get_device(device_id)

    assert device is not None
```

### Time

```python
from freezegun import freeze_time

@freeze_time("2024-01-15 10:30:00")
def test_time_based_trigger():
    trigger = create_time_trigger(interval_days=30)
    trigger.last_triggered_at = datetime(2024, 1, 1)

    # 14 days have passed, trigger should not fire
    assert not should_trigger(trigger)

@freeze_time("2024-02-15 10:30:00")
def test_time_based_trigger_fires():
    trigger = create_time_trigger(interval_days=30)
    trigger.last_triggered_at = datetime(2024, 1, 1)

    # 45 days have passed, trigger should fire
    assert should_trigger(trigger)
```

## Testing Best Practices

### 1. Clear Assertions

```python
# Bad - unclear what failed
assert response.status_code == 201

# Good - clear failure message
assert response.status_code == 201, (
    f"Expected 201, got {response.status_code}: {response.text}"
)
```

### 2. One Concept Per Test

```python
# Bad - testing multiple things
async def test_device_operations():
    # Create device
    device = await create_device()
    assert device.id

    # Generate token
    token = await generate_token(device.id)
    assert token

    # Submit metrics
    await submit_metrics(device.id)

# Good - separate tests
async def test_device_creation():
    device = await create_device()
    assert device.id is not None

async def test_token_generation(sample_device):
    token = await generate_token(sample_device.id)
    assert token is not None

async def test_metric_submission(sample_device):
    result = await submit_metrics(sample_device.id)
    assert result.success
```

### 3. Test Edge Cases

```python
class TestTokenValidation:
    async def test_valid_token(self):
        """Happy path."""
        pass

    async def test_expired_token(self):
        """Token past expiry."""
        pass

    async def test_malformed_token(self):
        """Invalid format."""
        pass

    async def test_replay_attack(self):
        """Same token used twice."""
        pass

    async def test_future_counter(self):
        """Counter too far ahead."""
        pass
```

### 4. Use Factories

```python
# conftest.py
class DeviceFactory:
    @staticmethod
    def create(
        external_id: str = None,
        device_type: DeviceType = DeviceType.SOLAR,
        status: DeviceStatus = DeviceStatus.ACTIVE,
    ) -> Device:
        return Device(
            external_id=external_id or f"test_{uuid4().hex[:8]}",
            device_type=device_type,
            status=status,
            openpaygo_secret_key="a" * 64,
        )

# Usage in tests
def test_inactive_device():
    device = DeviceFactory.create(status=DeviceStatus.INACTIVE)
    ...
```

### 5. Parametrized Tests

```python
@pytest.mark.parametrize("device_type,expected_metrics", [
    (DeviceType.SOLAR, ["energy_consumed", "energy_generated", "battery_level"]),
    (DeviceType.EMOBILITY, ["distance_traveled", "battery_level", "energy_consumed"]),
])
async def test_device_type_metrics(device_type, expected_metrics):
    device = DeviceFactory.create(device_type=device_type)
    supported = get_supported_metrics(device)

    for metric in expected_metrics:
        assert metric in supported
```

## Test Configuration

### pytest.ini

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
filterwarnings =
    ignore::DeprecationWarning
```

### Environment

```bash
# .env.test
DATABASE_URL=postgresql+asyncpg://test:test@localhost/paygo_test
REDIS_URL=redis://localhost:6379/1
SECRET_KEY=test-secret-key-minimum-32-chars!!
ENCRYPTION_KEY=test-encryption-key-32-chars!!
ENVIRONMENT=testing
```

## Continuous Integration

### GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: paygo_test
        ports:
          - 5432:5432

      redis:
        image: redis:7
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install poetry
          poetry install

      - name: Run tests
        run: poetry run pytest --cov=app --cov-report=xml
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:test@localhost/paygo_test
          REDIS_URL: redis://localhost:6379/0

      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

## Debugging Tests

### Verbose Output

```bash
pytest -vvv tests/unit/test_encryption.py
```

### Print Statements

```bash
pytest -s tests/unit/test_encryption.py
```

### Debug on Failure

```bash
pytest --pdb tests/unit/test_encryption.py
```

### Specific Test

```bash
pytest tests/unit/test_encryption.py::TestEncryption::test_encrypt_decrypt_roundtrip -vvv
```

## Performance Testing

### Load Testing with Locust

```python
# locustfile.py
from locust import HttpUser, task, between

class PaygoUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {"X-API-Key": "test_key"}

    @task(3)
    def register_device(self):
        self.client.post(
            "/api/v1/openpaygo/device/register",
            json={"external_id": f"load_test_{uuid4().hex}", "device_type": "solar"},
            headers=self.headers,
        )

    @task(10)
    def submit_metrics(self):
        self.client.post(
            "/api/v1/openpaygo/metrics",
            params={"device_id": self.device_id},
            json={"metric_type": "energy_consumed", "value": 10.0, "unit": "kWh"},
            headers=self.headers,
        )
```

Run with:

```bash
locust -f locustfile.py --host=http://localhost:8000
```
