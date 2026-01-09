#!/usr/bin/env python3
"""
Create Initial Admin User Script

This script creates the first admin user for the PAYGO Middleware system.
Run this after database migration to set up initial access.

Usage:
    python scripts/create_admin.py
    python scripts/create_admin.py --username admin --email admin@example.com

Environment:
    - Requires DATABASE_URL to be set
    - Reads from .env file if available
"""

import argparse
import asyncio
import getpass
import os
import re
import sys
from pathlib import Path

# Add the backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

# Load environment variables
from dotenv import load_dotenv

load_dotenv(backend_dir / ".env")


async def create_admin_user(
    username: str,
    email: str,
    password: str,
    full_name: str | None = None,
) -> dict:
    """
    Create an admin user in the database.

    Args:
        username: Admin username
        email: Admin email
        password: Admin password
        full_name: Optional display name

    Returns:
        User data dict
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    # Create engine
    engine = create_async_engine(str(settings.database_url), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Check if username exists
        result = await session.execute(
            select(User).where(User.username == username.lower())
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise ValueError(f"Username already exists: {username}")

        # Check if email exists
        result = await session.execute(
            select(User).where(User.email == email.lower())
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise ValueError(f"Email already exists: {email}")

        # Create admin user
        user = User(
            username=username.lower(),
            email=email.lower(),
            hashed_password=hash_password(password),
            full_name=full_name or "System Administrator",
            role=UserRole.ADMIN,
            is_active=True,
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)

        return user.to_dict_safe()

    await engine.dispose()


def validate_password(password: str) -> tuple[bool, str]:
    """
    Validate password meets requirements.

    Requirements:
    - Minimum 8 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit

    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters"

    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"

    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"

    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"

    return True, ""


def validate_email(email: str) -> tuple[bool, str]:
    """Validate email format."""
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return False, "Invalid email format"
    return True, ""


def validate_username(username: str) -> tuple[bool, str]:
    """Validate username format."""
    if len(username) < 3:
        return False, "Username must be at least 3 characters"

    if len(username) > 50:
        return False, "Username must not exceed 50 characters"

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", username):
        return False, "Username must start with a letter and contain only letters, numbers, underscores, and hyphens"

    return True, ""


async def main():
    parser = argparse.ArgumentParser(
        description="Create initial admin user for PAYGO Middleware"
    )
    parser.add_argument(
        "--username",
        type=str,
        default=None,
        help="Admin username (default: prompt)",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Admin email (default: prompt)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Admin password (default: prompt, recommended)",
    )
    parser.add_argument(
        "--full-name",
        type=str,
        default=None,
        help="Admin display name",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Don't prompt for missing values",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("PAYGO Middleware - Create Admin User")
    print("=" * 60)
    print()

    # Get username
    username = args.username
    if not username:
        if args.non_interactive:
            print("Error: --username required in non-interactive mode")
            sys.exit(1)
        while True:
            username = input("Username: ").strip()
            is_valid, error = validate_username(username)
            if is_valid:
                break
            print(f"  Error: {error}")

    # Get email
    email = args.email
    if not email:
        if args.non_interactive:
            print("Error: --email required in non-interactive mode")
            sys.exit(1)
        while True:
            email = input("Email: ").strip()
            is_valid, error = validate_email(email)
            if is_valid:
                break
            print(f"  Error: {error}")

    # Get password
    password = args.password
    if not password:
        if args.non_interactive:
            print("Error: --password required in non-interactive mode")
            sys.exit(1)
        while True:
            password = getpass.getpass("Password: ")
            is_valid, error = validate_password(password)
            if not is_valid:
                print(f"  Error: {error}")
                continue

            password_confirm = getpass.getpass("Confirm password: ")
            if password != password_confirm:
                print("  Error: Passwords do not match")
                continue

            break

    # Validate inputs
    is_valid, error = validate_username(username)
    if not is_valid:
        print(f"Error: {error}")
        sys.exit(1)

    is_valid, error = validate_email(email)
    if not is_valid:
        print(f"Error: {error}")
        sys.exit(1)

    is_valid, error = validate_password(password)
    if not is_valid:
        print(f"Error: {error}")
        sys.exit(1)

    # Get full name (optional)
    full_name = args.full_name
    if not full_name and not args.non_interactive:
        full_name = input("Full name (optional): ").strip() or None

    print()
    print("Creating admin user...")

    try:
        user_data = await create_admin_user(
            username=username,
            email=email,
            password=password,
            full_name=full_name,
        )

        print()
        print("=" * 60)
        print("Admin user created successfully!")
        print("=" * 60)
        print()
        print(f"  User ID:  {user_data['id']}")
        print(f"  Username: {user_data['username']}")
        print(f"  Email:    {user_data['email']}")
        print(f"  Role:     {user_data['role']}")
        print()
        print("You can now log in at POST /api/v1/auth/login")
        print()

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"Error creating user: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
