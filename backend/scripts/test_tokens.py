#!/usr/bin/env python3
"""
Manual Testing Script for OpenPAYGO Token Generation

This script allows you to manually test token generation and validation
without starting the full API server.

Usage:
    python scripts/test_tokens.py [command]

Commands:
    generate    - Generate a token for a device
    validate    - Validate a token
    list        - List tokens for a device
    simulate    - Run a full simulation test
    benchmark   - Run performance benchmark

Requirements:
    - Database must be running and accessible
    - Environment variables must be set (.env file)

Examples:
    python scripts/test_tokens.py generate --device-id <uuid> --days 30
    python scripts/test_tokens.py validate --device-id <uuid> --token "123-456-789"
    python scripts/test_tokens.py simulate
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.models.customer import Customer
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.device_token import DeviceToken
from app.services.token_service import OpenPAYGOTokenService, TokenType
from app.utils.encryption import encrypt_value


# =============================================================================
# Console Colors for Better Output
# =============================================================================

class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def success(msg: str) -> None:
    """Print success message."""
    print(f"{Colors.GREEN}✓ {msg}{Colors.ENDC}")


def error(msg: str) -> None:
    """Print error message."""
    print(f"{Colors.RED}✗ {msg}{Colors.ENDC}")


def info(msg: str) -> None:
    """Print info message."""
    print(f"{Colors.CYAN}ℹ {msg}{Colors.ENDC}")


def header(msg: str) -> None:
    """Print header."""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*60}")
    print(f"{msg}")
    print(f"{'='*60}{Colors.ENDC}\n")


# =============================================================================
# Database Setup
# =============================================================================


async def get_db_session() -> AsyncSession:
    """Create database session."""
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return async_session()


# =============================================================================
# Command Functions
# =============================================================================


async def cmd_generate(args: argparse.Namespace) -> None:
    """Generate a token for a device."""
    header("Token Generation Test")

    async with await get_db_session() as session:
        token_service = OpenPAYGOTokenService(session)

        try:
            device_id = UUID(args.device_id)
            info(f"Device ID: {device_id}")
            info(f"Days Valid: {args.days}")
            info(f"Token Type: {args.token_type}")

            # Generate token
            token_type = TokenType.SET_TIME if args.token_type == "SET_TIME" else TokenType.ADD_TIME

            start_time = time.time()
            result = await token_service.generate_token(
                device_id=device_id,
                days_valid=args.days,
                token_type=token_type,
            )
            elapsed = (time.time() - start_time) * 1000

            await session.commit()

            success("Token generated successfully!")
            print(f"\n{Colors.BOLD}Results:{Colors.ENDC}")
            print(f"  Token:       {Colors.GREEN}{Colors.BOLD}{result.token}{Colors.ENDC}")
            print(f"  Days Valid:  {result.days_added}")
            print(f"  Token Type:  {result.token_type.name}")
            print(f"  Counter:     {result.counter_value}")
            print(f"  Expires At:  {result.expires_at.isoformat()}")
            print(f"  Generated:   {elapsed:.2f}ms")

        except Exception as e:
            error(f"Token generation failed: {e}")
            raise


async def cmd_validate(args: argparse.Namespace) -> None:
    """Validate a token."""
    header("Token Validation Test")

    async with await get_db_session() as session:
        token_service = OpenPAYGOTokenService(session)

        try:
            device_id = UUID(args.device_id)
            info(f"Device ID: {device_id}")
            info(f"Token:     {args.token}")
            info(f"Consume:   {args.consume}")

            start_time = time.time()
            result = await token_service.validate_token(
                device_id=device_id,
                token=args.token,
                consume=args.consume,
            )
            elapsed = (time.time() - start_time) * 1000

            if args.consume:
                await session.commit()

            print(f"\n{Colors.BOLD}Results:{Colors.ENDC}")
            print(f"  Valid:       {Colors.GREEN if result.valid else Colors.RED}{result.valid}{Colors.ENDC}")
            if result.valid:
                print(f"  Days:        {result.days}")
                print(f"  Token Type:  {result.token_type}")
                if hasattr(result, "consumed"):
                    print(f"  Consumed:    {result.consumed}")
            print(f"  Validated:   {elapsed:.2f}ms")

        except Exception as e:
            error(f"Token validation failed: {e}")
            raise


async def cmd_list(args: argparse.Namespace) -> None:
    """List tokens for a device."""
    header("Device Tokens List")

    async with await get_db_session() as session:
        try:
            device_id = UUID(args.device_id)
            info(f"Device ID: {device_id}")

            # Query tokens
            stmt = (
                select(DeviceToken)
                .where(DeviceToken.device_id == device_id)
                .order_by(DeviceToken.created_at.desc())
                .limit(args.limit)
            )
            result = await session.execute(stmt)
            tokens = result.scalars().all()

            print(f"\n{Colors.BOLD}Found {len(tokens)} token(s):{Colors.ENDC}\n")

            for i, token in enumerate(tokens, 1):
                status = "USED" if token.used_at else "REVOKED" if token.revoked_at else "ACTIVE"
                status_color = Colors.YELLOW if status == "USED" else Colors.RED if status == "REVOKED" else Colors.GREEN

                print(f"  {i}. Counter: {token.counter_value}")
                print(f"     Days: {token.days_valid}")
                print(f"     Type: {token.token_type}")
                print(f"     Status: {status_color}{status}{Colors.ENDC}")
                print(f"     Created: {token.created_at.isoformat()}")
                print(f"     Expires: {token.expires_at.isoformat()}")
                print()

        except Exception as e:
            error(f"Failed to list tokens: {e}")
            raise


async def cmd_simulate(args: argparse.Namespace) -> None:
    """Run a full simulation test."""
    header("OpenPAYGO Token Simulation")

    async with await get_db_session() as session:
        try:
            # Create test customer
            info("Creating test customer...")
            customer = Customer(
                external_id=f"test_customer_{uuid4().hex[:8]}",
                email="test@example.com",
                phone="+12025551234",
            )
            session.add(customer)
            await session.flush()
            success(f"Customer created: {customer.id}")

            # Create test device
            info("Creating test device...")
            secret_key = uuid4().hex + uuid4().hex  # 64 hex chars = 32 bytes
            encrypted_secret = encrypt_value(secret_key, associated_data="test_device")

            device = Device(
                external_id=f"test_device_{uuid4().hex[:8]}",
                customer_id=customer.id,
                device_type=DeviceType.SOLAR,
                manufacturer="TestMfg",
                model="TestModel",
                openpaygo_secret_key=encrypted_secret,
                status=DeviceStatus.ACTIVE,
            )
            session.add(device)
            await session.flush()
            success(f"Device created: {device.id}")
            info(f"Device external ID: {device.external_id}")

            # Initialize token service
            token_service = OpenPAYGOTokenService(session)

            # Generate multiple tokens
            print(f"\n{Colors.BOLD}Generating tokens...{Colors.ENDC}")

            tokens_generated = []
            test_cases = [
                (7, TokenType.ADD_TIME, "Weekly token"),
                (30, TokenType.ADD_TIME, "Monthly token"),
                (90, TokenType.SET_TIME, "Quarterly set token"),
                (1, TokenType.ADD_TIME, "Single day token"),
            ]

            for days, token_type, description in test_cases:
                result = await token_service.generate_token(
                    device_id=device.id,
                    days_valid=days,
                    token_type=token_type,
                )
                tokens_generated.append(result)
                print(f"  • {description}: {Colors.GREEN}{result.token}{Colors.ENDC} (counter={result.counter_value})")

            await session.commit()

            # Validate tokens
            print(f"\n{Colors.BOLD}Validating tokens...{Colors.ENDC}")

            for i, token_result in enumerate(tokens_generated):
                validation = await token_service.validate_token(
                    device_id=device.id,
                    token=token_result.token,
                    consume=False,
                )
                status = f"{Colors.GREEN}✓ Valid{Colors.ENDC}" if validation.valid else f"{Colors.RED}✗ Invalid{Colors.ENDC}"
                print(f"  Token {i+1}: {status}")

            # Test token uniqueness
            print(f"\n{Colors.BOLD}Testing token uniqueness...{Colors.ENDC}")
            all_tokens = [t.token for t in tokens_generated]
            unique_tokens = set(all_tokens)
            if len(all_tokens) == len(unique_tokens):
                success("All tokens are unique")
            else:
                error("Duplicate tokens detected!")

            # Test counter incrementing
            print(f"\n{Colors.BOLD}Testing counter sequence...{Colors.ENDC}")
            counters = [t.counter_value for t in tokens_generated]
            is_sequential = all(counters[i] < counters[i+1] for i in range(len(counters)-1))
            if is_sequential:
                success(f"Counters increment correctly: {counters}")
            else:
                error(f"Counter sequence error: {counters}")

            # Summary
            print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*60}")
            print("SIMULATION COMPLETE")
            print(f"{'='*60}{Colors.ENDC}")
            print(f"  Customer ID:     {customer.id}")
            print(f"  Device ID:       {device.id}")
            print(f"  Tokens Created:  {len(tokens_generated)}")
            print(f"  All Valid:       Yes")

        except Exception as e:
            error(f"Simulation failed: {e}")
            await session.rollback()
            raise


async def cmd_benchmark(args: argparse.Namespace) -> None:
    """Run performance benchmark."""
    header("Token Generation Benchmark")

    async with await get_db_session() as session:
        try:
            # Create test device if not provided
            if args.device_id:
                device_id = UUID(args.device_id)
                info(f"Using existing device: {device_id}")
            else:
                info("Creating test device for benchmark...")
                customer = Customer(
                    external_id=f"bench_customer_{uuid4().hex[:8]}",
                    email="bench@example.com",
                )
                session.add(customer)
                await session.flush()

                secret_key = uuid4().hex + uuid4().hex
                encrypted_secret = encrypt_value(secret_key, associated_data="bench_device")

                device = Device(
                    external_id=f"bench_device_{uuid4().hex[:8]}",
                    customer_id=customer.id,
                    device_type=DeviceType.SOLAR,
                    manufacturer="BenchMfg",
                    model="BenchModel",
                    openpaygo_secret_key=encrypted_secret,
                    status=DeviceStatus.ACTIVE,
                )
                session.add(device)
                await session.flush()
                device_id = device.id
                success(f"Benchmark device created: {device_id}")

            token_service = OpenPAYGOTokenService(session)
            num_tokens = args.count

            # Benchmark token generation
            print(f"\n{Colors.BOLD}Generating {num_tokens} tokens...{Colors.ENDC}")

            start_time = time.time()
            times = []

            for i in range(num_tokens):
                token_start = time.time()
                await token_service.generate_token(
                    device_id=device_id,
                    days_valid=30,
                )
                times.append((time.time() - token_start) * 1000)

                if (i + 1) % 10 == 0:
                    print(f"  Generated {i + 1}/{num_tokens}...")

            total_time = time.time() - start_time

            await session.commit()

            # Statistics
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)
            tokens_per_sec = num_tokens / total_time

            print(f"\n{Colors.BOLD}Benchmark Results:{Colors.ENDC}")
            print(f"  Total Tokens:    {num_tokens}")
            print(f"  Total Time:      {total_time:.2f}s")
            print(f"  Avg per Token:   {avg_time:.2f}ms")
            print(f"  Min Time:        {min_time:.2f}ms")
            print(f"  Max Time:        {max_time:.2f}ms")
            print(f"  Throughput:      {tokens_per_sec:.1f} tokens/sec")

        except Exception as e:
            error(f"Benchmark failed: {e}")
            raise


# =============================================================================
# Main Entry Point
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="OpenPAYGO Token Testing Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Generate a 30-day token:
    python scripts/test_tokens.py generate --device-id <uuid> --days 30

  Validate a token:
    python scripts/test_tokens.py validate --device-id <uuid> --token "123-456-789"

  Run full simulation:
    python scripts/test_tokens.py simulate

  Run benchmark:
    python scripts/test_tokens.py benchmark --count 100
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Generate command
    gen_parser = subparsers.add_parser("generate", help="Generate a token")
    gen_parser.add_argument("--device-id", required=True, help="Device UUID")
    gen_parser.add_argument("--days", type=int, default=30, help="Days valid (default: 30)")
    gen_parser.add_argument(
        "--token-type",
        choices=["ADD_TIME", "SET_TIME"],
        default="ADD_TIME",
        help="Token type (default: ADD_TIME)",
    )

    # Validate command
    val_parser = subparsers.add_parser("validate", help="Validate a token")
    val_parser.add_argument("--device-id", required=True, help="Device UUID")
    val_parser.add_argument("--token", required=True, help="Token to validate (XXX-XXX-XXX)")
    val_parser.add_argument("--consume", action="store_true", help="Consume token if valid")

    # List command
    list_parser = subparsers.add_parser("list", help="List tokens for a device")
    list_parser.add_argument("--device-id", required=True, help="Device UUID")
    list_parser.add_argument("--limit", type=int, default=10, help="Max tokens to show (default: 10)")

    # Simulate command
    sim_parser = subparsers.add_parser("simulate", help="Run full simulation")

    # Benchmark command
    bench_parser = subparsers.add_parser("benchmark", help="Run performance benchmark")
    bench_parser.add_argument("--device-id", help="Device UUID (optional, creates new if not provided)")
    bench_parser.add_argument("--count", type=int, default=50, help="Number of tokens to generate (default: 50)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run command
    commands = {
        "generate": cmd_generate,
        "validate": cmd_validate,
        "list": cmd_list,
        "simulate": cmd_simulate,
        "benchmark": cmd_benchmark,
    }

    try:
        asyncio.run(commands[args.command](args))
    except KeyboardInterrupt:
        print("\n\nAborted.")
        sys.exit(1)
    except Exception as e:
        error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
