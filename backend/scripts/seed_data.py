#!/usr/bin/env python3
"""
Seed Database with Sample Data

Creates sample devices, customers, and transactions for development/testing.

Usage:
    python scripts/seed_data.py
    python scripts/seed_data.py --devices 10 --customers 5
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import generate_api_key, generate_device_secret, hash_password
from app.db.session import async_session
from app.models.customer import Customer
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_activation import ActivationType, DeviceActivation
from app.models.device_metric import DeviceMetric
from app.models.payment_trigger import PaymentTrigger, TriggerStatus, TriggerType
from app.models.transaction import PaymentProvider, Transaction, TransactionStatus
from app.utils.encryption import encrypt_value

logger = get_logger(__name__)


async def create_sample_customers(session: AsyncSession, count: int) -> list[Customer]:
    """Create sample customers."""
    customers = []

    for i in range(count):
        customer = Customer(
            external_id=f"customer_{i+1:03d}",
            email=f"customer{i+1}@example.com",
            phone=f"+1555{i+1:07d}",
            name=f"Test Customer {i+1}",
            preferred_provider=PaymentProvider.WAVE if i % 2 == 0 else PaymentProvider.QMONEY,
            metadata={"source": "seed_script", "tier": "standard"},
        )
        session.add(customer)
        customers.append(customer)

    await session.flush()
    logger.info(f"Created {len(customers)} sample customers")
    return customers


async def create_sample_devices(session: AsyncSession, count: int) -> list[Device]:
    """Create sample devices."""
    devices = []
    device_types = list(DeviceType)

    for i in range(count):
        device_type = device_types[i % len(device_types)]

        # Generate and encrypt secret key
        secret = generate_device_secret()
        encrypted_secret = encrypt_value(secret)

        device = Device(
            external_id=f"device_{device_type.value}_{i+1:03d}",
            device_type=device_type,
            manufacturer="SampleMfg" if device_type == DeviceType.SOLAR else "EcoMotors",
            model=f"Model-{i+1}",
            firmware_version="1.0.0",
            openpaygo_secret_key=encrypted_secret,
            openpaygo_token_count=0,
            status=DeviceStatus.ACTIVE,
            metadata={
                "serial_number": f"SN{uuid4().hex[:12].upper()}",
                "installation_date": datetime.now(timezone.utc).isoformat(),
            },
        )
        session.add(device)
        devices.append(device)

    await session.flush()
    logger.info(f"Created {len(devices)} sample devices")
    return devices


async def create_sample_transactions(
    session: AsyncSession,
    customers: list[Customer],
    devices: list[Device],
) -> list[Transaction]:
    """Create sample transactions."""
    transactions = []

    for i, device in enumerate(devices):
        customer = customers[i % len(customers)]

        # Create a completed transaction
        transaction = Transaction(
            customer_id=customer.id,
            device_id=device.id,
            amount=Decimal("50.00") + Decimal(i * 10),
            currency="USD",
            provider=customer.preferred_provider,
            provider_transaction_id=f"prov_txn_{uuid4().hex[:12]}",
            status=TransactionStatus.COMPLETED,
            idempotency_key=f"seed_{device.id}_{uuid4().hex[:8]}",
            completed_at=datetime.now(timezone.utc) - timedelta(hours=i),
        )
        session.add(transaction)
        transactions.append(transaction)

    await session.flush()
    logger.info(f"Created {len(transactions)} sample transactions")
    return transactions


async def create_sample_activations(
    session: AsyncSession,
    devices: list[Device],
    transactions: list[Transaction],
) -> list[DeviceActivation]:
    """Create sample device activations."""
    activations = []

    for device, transaction in zip(devices, transactions):
        activation = DeviceActivation(
            device_id=device.id,
            transaction_id=transaction.id,
            activation_type=ActivationType.ADD_TIME,
            token_value="123-456-789",  # Sample token
            days_added=30,
            activated_at=transaction.completed_at,
            expires_at=transaction.completed_at + timedelta(days=30),
        )
        session.add(activation)
        activations.append(activation)

    await session.flush()
    logger.info(f"Created {len(activations)} sample activations")
    return activations


async def create_sample_metrics(
    session: AsyncSession,
    devices: list[Device],
) -> list[DeviceMetric]:
    """Create sample device metrics."""
    metrics = []
    now = datetime.now(timezone.utc)

    for device in devices:
        # Create metrics for past 7 days
        for day in range(7):
            timestamp = now - timedelta(days=day)

            if device.device_type == DeviceType.SOLAR:
                # Solar device metrics
                metrics.extend([
                    DeviceMetric(
                        device_id=device.id,
                        metric_type="energy_generated",
                        value=Decimal(f"{5.0 + day * 0.5:.2f}"),
                        unit="kWh",
                        recorded_at=timestamp,
                    ),
                    DeviceMetric(
                        device_id=device.id,
                        metric_type="battery_level",
                        value=Decimal(f"{85.0 - day * 2:.1f}"),
                        unit="%",
                        recorded_at=timestamp,
                    ),
                ])
            elif device.device_type == DeviceType.EMOBILITY:
                # E-mobility metrics
                metrics.extend([
                    DeviceMetric(
                        device_id=device.id,
                        metric_type="distance_traveled",
                        value=Decimal(f"{25.0 + day * 5:.1f}"),
                        unit="km",
                        recorded_at=timestamp,
                    ),
                    DeviceMetric(
                        device_id=device.id,
                        metric_type="energy_consumed",
                        value=Decimal(f"{3.0 + day * 0.5:.2f}"),
                        unit="kWh",
                        recorded_at=timestamp,
                    ),
                ])
            else:
                # Generic metrics
                metrics.append(
                    DeviceMetric(
                        device_id=device.id,
                        metric_type="uptime",
                        value=Decimal(f"{24.0 * (day + 1):.1f}"),
                        unit="hours",
                        recorded_at=timestamp,
                    )
                )

    for metric in metrics:
        session.add(metric)

    await session.flush()
    logger.info(f"Created {len(metrics)} sample metrics")
    return metrics


async def create_sample_triggers(
    session: AsyncSession,
    devices: list[Device],
) -> list[PaymentTrigger]:
    """Create sample payment triggers."""
    triggers = []

    for i, device in enumerate(devices):
        # Alternate trigger types
        if i % 3 == 0:
            trigger = PaymentTrigger(
                device_id=device.id,
                trigger_type=TriggerType.USAGE_BASED,
                status=TriggerStatus.ACTIVE,
                configuration={
                    "metric_type": "energy_consumed",
                    "threshold": 100.0,
                    "amount_per_unit": "0.10",
                    "currency": "USD",
                },
            )
        elif i % 3 == 1:
            trigger = PaymentTrigger(
                device_id=device.id,
                trigger_type=TriggerType.TIME_BASED,
                status=TriggerStatus.ACTIVE,
                configuration={
                    "interval_days": 30,
                    "amount": "25.00",
                    "currency": "USD",
                },
            )
        else:
            trigger = PaymentTrigger(
                device_id=device.id,
                trigger_type=TriggerType.MANUAL,
                status=TriggerStatus.ACTIVE,
                configuration={
                    "amount": "10.00",
                    "currency": "USD",
                    "description": "Manual top-up",
                },
            )

        session.add(trigger)
        triggers.append(trigger)

    await session.flush()
    logger.info(f"Created {len(triggers)} sample triggers")
    return triggers


async def seed_database(device_count: int, customer_count: int):
    """Main seeding function."""
    logger.info("Starting database seeding...")

    async with async_session() as session:
        try:
            # Check if data already exists
            result = await session.execute(select(Device).limit(1))
            if result.scalars().first():
                logger.warning("Database already contains data. Skipping seed.")
                click.echo("Database already seeded. Use --force to re-seed (will clear existing data).")
                return

            # Create sample data
            customers = await create_sample_customers(session, customer_count)
            devices = await create_sample_devices(session, device_count)
            transactions = await create_sample_transactions(session, customers, devices)
            await create_sample_activations(session, devices, transactions)
            await create_sample_metrics(session, devices)
            await create_sample_triggers(session, devices)

            await session.commit()

            logger.info("Database seeding completed successfully!")
            click.echo(f"\nSeeded database with:")
            click.echo(f"  - {customer_count} customers")
            click.echo(f"  - {device_count} devices")
            click.echo(f"  - {len(transactions)} transactions")
            click.echo(f"  - Sample metrics and triggers")

        except Exception as e:
            await session.rollback()
            logger.error(f"Seeding failed: {e}")
            raise


@click.command()
@click.option("--devices", default=5, help="Number of devices to create")
@click.option("--customers", default=3, help="Number of customers to create")
def main(devices: int, customers: int):
    """Seed the database with sample data."""
    click.echo("PAYGO Middleware - Database Seeder")
    click.echo("=" * 40)

    asyncio.run(seed_database(devices, customers))


if __name__ == "__main__":
    main()
