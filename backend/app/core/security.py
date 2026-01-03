"""
Security Utilities for PAYGO Middleware

This module provides:
- API key authentication
- JWT token handling
- Password hashing
- Encryption utilities
- Webhook signature verification

CRITICAL: All security operations log failures loudly.
Never log actual secrets, tokens, or passwords.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.logging import audit_logger, get_logger

logger = get_logger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# =============================================================================
# API Key Authentication
# =============================================================================


def verify_api_key(api_key: str, expected_key: str, key_name: str = "api_key") -> bool:
    """
    Verify an API key using constant-time comparison.

    LOUD FAILURE: Logs all verification attempts.
    Never logs the actual keys.

    Args:
        api_key: The API key to verify
        expected_key: The expected API key
        key_name: Name of the key for logging

    Returns:
        True if valid, False otherwise
    """
    if not api_key or not expected_key:
        logger.warning(
            "API key verification failed: empty key provided",
            key_name=key_name,
        )
        return False

    # Constant-time comparison to prevent timing attacks
    is_valid = secrets.compare_digest(api_key.encode(), expected_key.encode())

    if is_valid:
        logger.debug("API key verified successfully", key_name=key_name)
    else:
        logger.warning(
            "API key verification failed: key mismatch",
            key_name=key_name,
            provided_key_length=len(api_key),
        )

    return is_valid


def verify_admin_api_key(api_key: str) -> bool:
    """
    Verify admin API key.

    LOUD: Logs all admin authentication attempts.
    """
    is_valid = verify_api_key(api_key, settings.admin_api_key, "admin_api_key")

    audit_logger.log_event(
        event_type="auth",
        action="admin_api_key_verify",
        outcome="success" if is_valid else "failure",
        details={"key_provided": bool(api_key)},
    )

    return is_valid


def generate_api_key(prefix: str = "paygo") -> str:
    """
    Generate a new API key.

    Format: {prefix}_{random_32_chars}

    Args:
        prefix: Key prefix for identification

    Returns:
        Generated API key
    """
    random_part = secrets.token_urlsafe(32)
    key = f"{prefix}_{random_part}"

    logger.info(
        "Generated new API key",
        prefix=prefix,
        key_length=len(key),
    )

    return key


# =============================================================================
# JWT Token Handling
# =============================================================================

JWT_ALGORITHM = "HS256"


def create_access_token(
    subject: str | UUID,
    *,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """
    Create a JWT access token.

    LOUD: Logs token creation with subject (but not the token itself).

    Args:
        subject: Token subject (usually user ID)
        expires_delta: Custom expiration time
        extra_claims: Additional claims to include

    Returns:
        Encoded JWT token
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_expiration_minutes)

    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    claims = {
        "sub": str(subject),
        "iat": now,
        "exp": expire,
        "type": "access",
    }

    if extra_claims:
        claims.update(extra_claims)

    try:
        token = jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM)
        logger.debug(
            "JWT token created",
            subject=str(subject),
            expires_in_minutes=expires_delta.total_seconds() / 60,
        )
        return token

    except Exception as e:
        logger.error(
            "Failed to create JWT token",
            subject=str(subject),
            error=str(e),
        )
        raise AuthenticationError(
            "Failed to create access token",
            context={"subject": str(subject)},
        )


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    LOUD FAILURE: Logs all decode failures with reason.

    Args:
        token: The JWT token to decode

    Returns:
        Token claims if valid

    Raises:
        AuthenticationError: If token is invalid
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])

        # Validate token type
        if payload.get("type") != "access":
            logger.warning("Invalid token type", token_type=payload.get("type"))
            raise AuthenticationError(
                "Invalid token type",
                context={"expected": "access", "got": payload.get("type")},
            )

        logger.debug("JWT token decoded successfully", subject=payload.get("sub"))
        return payload

    except JWTError as e:
        logger.warning("JWT decode failed", error=str(e))
        raise AuthenticationError(
            "Invalid or expired token",
            context={"error_type": type(e).__name__},
        )


# =============================================================================
# Password Hashing
# =============================================================================


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.

    LOUD: Logs password hash operations (never the password).

    Args:
        password: Plain text password

    Returns:
        Hashed password
    """
    if not password:
        logger.error("Attempted to hash empty password")
        raise ValueError("Password cannot be empty")

    hashed = pwd_context.hash(password)
    logger.debug("Password hashed successfully")
    return hashed


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash.

    LOUD: Logs verification attempts (never the password).

    Args:
        plain_password: Plain text password to verify
        hashed_password: Stored password hash

    Returns:
        True if password matches
    """
    if not plain_password or not hashed_password:
        logger.warning("Password verification with empty value")
        return False

    try:
        is_valid = pwd_context.verify(plain_password, hashed_password)
        logger.debug("Password verification", result="success" if is_valid else "failure")
        return is_valid
    except Exception as e:
        logger.error("Password verification error", error=str(e))
        return False


# =============================================================================
# Webhook Signature Verification
# =============================================================================


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
    provider: str,
) -> bool:
    """
    Verify webhook signature using HMAC-SHA256.

    LOUD: Logs all verification attempts with provider name.

    Args:
        payload: Raw webhook payload bytes
        signature: Provided signature
        secret: Webhook secret key
        provider: Provider name for logging

    Returns:
        True if signature is valid
    """
    if not payload or not signature or not secret:
        logger.warning(
            "Webhook verification failed: missing data",
            provider=provider,
            has_payload=bool(payload),
            has_signature=bool(signature),
            has_secret=bool(secret),
        )
        return False

    try:
        # Compute expected signature
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        # Constant-time comparison
        is_valid = hmac.compare_digest(signature.lower(), expected.lower())

        if is_valid:
            logger.info(
                "Webhook signature verified",
                provider=provider,
            )
        else:
            logger.warning(
                "Webhook signature mismatch",
                provider=provider,
                signature_length=len(signature),
            )

        return is_valid

    except Exception as e:
        logger.error(
            "Webhook signature verification error",
            provider=provider,
            error=str(e),
        )
        return False


def generate_webhook_test_signature(payload: bytes, secret: str) -> str:
    """
    Generate a webhook signature for testing.

    Args:
        payload: Payload bytes
        secret: Signing secret

    Returns:
        HMAC-SHA256 signature
    """
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


# =============================================================================
# Device Authentication
# =============================================================================


def generate_device_secret() -> str:
    """
    Generate a cryptographically secure device secret.

    This secret is used for OpenPAYGO token generation.

    Returns:
        32-byte hex secret (64 characters)
    """
    secret = secrets.token_hex(32)
    logger.debug("Device secret generated", secret_length=len(secret))
    return secret


def hash_device_token(token: str) -> str:
    """
    Hash a device token for storage/comparison.

    Args:
        token: Device activation token

    Returns:
        SHA-256 hash of token
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# =============================================================================
# Idempotency Key Generation
# =============================================================================


def generate_idempotency_key(
    prefix: str = "",
    *args: str,
) -> str:
    """
    Generate an idempotency key from components.

    Args:
        prefix: Key prefix
        args: Components to include in key

    Returns:
        Idempotency key as hex string
    """
    components = [prefix] + list(args) + [secrets.token_hex(8)]
    data = ":".join(str(c) for c in components)
    key = hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]

    logger.debug("Generated idempotency key", prefix=prefix)
    return key


# =============================================================================
# Authorization Helpers
# =============================================================================


def require_admin(api_key: str) -> None:
    """
    Require admin access or raise exception.

    LOUD: Raises AuthorizationError with details if not admin.

    Args:
        api_key: API key to verify

    Raises:
        AuthorizationError: If not admin
    """
    if not verify_admin_api_key(api_key):
        logger.warning("Admin access denied", api_key_provided=bool(api_key))
        raise AuthorizationError(
            resource="admin",
            action="access",
        )

    logger.info("Admin access granted")
