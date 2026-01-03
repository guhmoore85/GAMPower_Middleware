"""
Pytest Configuration and Fixtures

Provides fixtures for:
- Test database setup/teardown
- Test client for API testing
- Sample data factories
- Mock services

CRITICAL: All tests run in transactions that are rolled back.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator, Generator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.models.customer import Customer
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.transaction import PaymentProvider, Transaction, TransactionStatus
from app.utils.encryption import encrypt_value


# =============================================================================
# Event Loop
# =============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """Create test database engine."""
    # Use SQLite for testing (faster, no external dependencies)
    engine = create_async_engine(
        "sqlite+aiosqlite:///./test.db",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session with transaction rollback."""
    async_session = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with async_session() as session:
        async with session.begin():
            yield session
            await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with database override."""
    from app.core.dependencies import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# =============================================================================
# Sample Data Factories
# =============================================================================


@pytest.fixture
def sample_customer_data() -> dict:
    """Sample customer data for testing."""
    return {
        "external_id": f"customer_{uuid4().hex[:8]}",
        "email": "test@example.com",
        "phone": "+12025551234",
        "payment_provider": PaymentProvider.WAVE,
        "payment_provider_id": f"wave_{uuid4().hex[:16]}",
        "metadata_": {"test": True},
    }


@pytest.fixture
def sample_device_data() -> dict:
    """Sample device data for testing."""
    # Generate and encrypt a secret key
    secret_key = uuid4().hex
    encrypted_secret = encrypt_value(
        secret_key,
        associated_data="test_device",
    )

    return {
        "external_id": f"device_{uuid4().hex[:8]}",
        "device_type": DeviceType.SOLAR,
        "manufacturer": "TestMfg",
        "model": "TestModel",
        "openpaygo_secret_key": encrypted_secret,
        "status": DeviceStatus.ACTIVE,
        "metadata_": {"test": True},
    }


@pytest_asyncio.fixture
async def sample_customer(
    db_session: AsyncSession,
    sample_customer_data: dict,
) -> Customer:
    """Create a sample customer in the database."""
    customer = Customer(**sample_customer_data)
    db_session.add(customer)
    await db_session.flush()
    await db_session.refresh(customer)
    return customer


@pytest_asyncio.fixture
async def sample_device(
    db_session: AsyncSession,
    sample_device_data: dict,
    sample_customer: Customer,
) -> Device:
    """Create a sample device in the database."""
    sample_device_data["customer_id"] = sample_customer.id
    device = Device(**sample_device_data)
    db_session.add(device)
    await db_session.flush()
    await db_session.refresh(device)
    return device


@pytest_asyncio.fixture
async def sample_transaction(
    db_session: AsyncSession,
    sample_customer: Customer,
    sample_device: Device,
) -> Transaction:
    """Create a sample transaction in the database."""
    transaction = Transaction(
        customer_id=sample_customer.id,
        device_id=sample_device.id,
        payment_provider=PaymentProvider.WAVE,
        provider_transaction_id=f"wave_txn_{uuid4().hex[:16]}",
        amount=Decimal("50.00"),
        currency="USD",
        status=TransactionStatus.PENDING,
        idempotency_key=f"idem_{uuid4().hex[:16]}",
        metadata_={"test": True},
    )
    db_session.add(transaction)
    await db_session.flush()
    await db_session.refresh(transaction)
    return transaction


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_api_key() -> str:
    """Mock API key for testing."""
    return "test-api-key-for-development-only"


@pytest.fixture
def mock_admin_key() -> str:
    """Mock admin API key for testing."""
    return settings.admin_api_key


@pytest.fixture
def auth_headers(mock_api_key: str) -> dict:
    """Authentication headers for API requests."""
    return {"X-API-Key": mock_api_key}


@pytest.fixture
def admin_headers(mock_admin_key: str) -> dict:
    """Admin authentication headers for API requests."""
    return {"X-API-Key": mock_admin_key}


# =============================================================================
# Utility Functions
# =============================================================================


def assert_error_response(response, expected_code: str):
    """Assert response is an error with expected code."""
    assert response.status_code >= 400
    data = response.json()
    assert "error_code" in data
    assert data["error_code"] == expected_code


def assert_success_response(response, status_code: int = 200):
    """Assert response is successful."""
    assert response.status_code == status_code
    return response.json()
