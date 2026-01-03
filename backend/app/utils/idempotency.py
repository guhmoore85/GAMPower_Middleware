"""
Idempotency Key Handling for PAYGO Middleware

Prevents duplicate processing of payments and other operations.

CRITICAL: All idempotency checks are logged for debugging.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import redis.asyncio as redis

from app.core.config import settings
from app.core.exceptions import IdempotencyError
from app.core.logging import get_logger

logger = get_logger(__name__)


class IdempotencyManager:
    """
    Manages idempotency keys using Redis.

    Keys are stored with TTL to prevent indefinite storage.
    When a duplicate request is detected, returns the cached response.
    """

    # Key prefix for Redis
    KEY_PREFIX = "idempotency:"

    # Default TTL: 24 hours
    DEFAULT_TTL_SECONDS = 86400

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize idempotency manager.

        Args:
            redis_client: Async Redis client instance
        """
        self.redis = redis_client

    def _make_key(self, idempotency_key: str) -> str:
        """Generate Redis key from idempotency key."""
        return f"{self.KEY_PREFIX}{idempotency_key}"

    async def check_and_set(
        self,
        idempotency_key: str,
        operation: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> tuple[bool, Optional[dict]]:
        """
        Check if idempotency key exists and set if not.

        LOUD: Logs all checks with key and operation.

        Args:
            idempotency_key: The idempotency key
            operation: Operation name for logging
            ttl_seconds: TTL for the key

        Returns:
            Tuple of (is_new, cached_result)
            - is_new=True, cached_result=None: New request, proceed
            - is_new=False, cached_result=dict: Duplicate, return cached

        Raises:
            IdempotencyError: If there's an error checking/setting
        """
        key = self._make_key(idempotency_key)

        try:
            # Try to get existing entry
            existing = await self.redis.get(key)

            if existing:
                # Parse cached data
                cached_data = json.loads(existing)

                logger.info(
                    "Idempotency key found - duplicate request",
                    idempotency_key=idempotency_key,
                    operation=operation,
                    original_timestamp=cached_data.get("timestamp"),
                    status=cached_data.get("status"),
                )

                return False, cached_data

            # Set initial entry (in_progress status)
            entry = {
                "status": "in_progress",
                "operation": operation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": None,
            }

            # Use SET NX (only if not exists) for atomic operation
            was_set = await self.redis.set(
                key,
                json.dumps(entry),
                nx=True,
                ex=ttl_seconds,
            )

            if not was_set:
                # Another request beat us - get the existing data
                existing = await self.redis.get(key)
                if existing:
                    cached_data = json.loads(existing)
                    logger.info(
                        "Idempotency race condition - another request won",
                        idempotency_key=idempotency_key,
                        operation=operation,
                    )
                    return False, cached_data
                else:
                    # Edge case: key was set and expired between our SET and GET
                    logger.warning(
                        "Idempotency key race condition with expiry",
                        idempotency_key=idempotency_key,
                        operation=operation,
                    )
                    # Retry the set
                    await self.redis.set(key, json.dumps(entry), ex=ttl_seconds)

            logger.debug(
                "Idempotency key set - new request",
                idempotency_key=idempotency_key,
                operation=operation,
            )

            return True, None

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse idempotency cache data",
                idempotency_key=idempotency_key,
                error=str(e),
            )
            # Return as new request to allow processing
            return True, None

        except Exception as e:
            logger.error(
                "Idempotency check failed",
                idempotency_key=idempotency_key,
                operation=operation,
                error=str(e),
                error_type=type(e).__name__,
            )
            # On Redis error, we should fail loudly
            # Don't silently proceed as this could cause duplicates
            raise IdempotencyError(
                idempotency_key=idempotency_key,
                context={"operation": operation, "error": str(e)},
            )

    async def complete(
        self,
        idempotency_key: str,
        result: dict[str, Any],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        """
        Mark operation as completed and cache result.

        LOUD: Logs completion with status.

        Args:
            idempotency_key: The idempotency key
            result: Result to cache
            ttl_seconds: TTL for the completed entry
        """
        key = self._make_key(idempotency_key)

        try:
            entry = {
                "status": "completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": result,
            }

            await self.redis.set(key, json.dumps(entry, default=str), ex=ttl_seconds)

            logger.info(
                "Idempotency operation completed",
                idempotency_key=idempotency_key,
            )

        except Exception as e:
            logger.error(
                "Failed to mark idempotency as complete",
                idempotency_key=idempotency_key,
                error=str(e),
            )
            # Don't raise - the operation succeeded, just caching failed

    async def fail(
        self,
        idempotency_key: str,
        error: str,
        ttl_seconds: int = 3600,  # Shorter TTL for failures
    ) -> None:
        """
        Mark operation as failed.

        Failed operations can be retried after TTL expires.

        Args:
            idempotency_key: The idempotency key
            error: Error message
            ttl_seconds: TTL for failed entry (shorter to allow retry)
        """
        key = self._make_key(idempotency_key)

        try:
            entry = {
                "status": "failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": error,
            }

            await self.redis.set(key, json.dumps(entry), ex=ttl_seconds)

            logger.info(
                "Idempotency operation failed",
                idempotency_key=idempotency_key,
                error=error,
            )

        except Exception as e:
            logger.error(
                "Failed to mark idempotency as failed",
                idempotency_key=idempotency_key,
                error=str(e),
            )

    async def delete(self, idempotency_key: str) -> bool:
        """
        Delete an idempotency key.

        Use with caution - this allows duplicate processing.

        Args:
            idempotency_key: The key to delete

        Returns:
            True if key was deleted
        """
        key = self._make_key(idempotency_key)

        try:
            result = await self.redis.delete(key)

            if result:
                logger.warning(
                    "Idempotency key manually deleted",
                    idempotency_key=idempotency_key,
                )
            else:
                logger.debug(
                    "Idempotency key not found for deletion",
                    idempotency_key=idempotency_key,
                )

            return bool(result)

        except Exception as e:
            logger.error(
                "Failed to delete idempotency key",
                idempotency_key=idempotency_key,
                error=str(e),
            )
            return False

    async def get_status(self, idempotency_key: str) -> Optional[dict]:
        """
        Get the current status of an idempotency key.

        Args:
            idempotency_key: The key to check

        Returns:
            Status dict or None if not found
        """
        key = self._make_key(idempotency_key)

        try:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
            return None

        except Exception as e:
            logger.error(
                "Failed to get idempotency status",
                idempotency_key=idempotency_key,
                error=str(e),
            )
            return None


def generate_idempotency_key(
    provider: str,
    transaction_id: str,
    *,
    extra: Optional[str] = None,
) -> str:
    """
    Generate an idempotency key for a transaction.

    Format: SHA256 hash of provider:transaction_id[:extra]

    Args:
        provider: Payment provider name
        transaction_id: Provider's transaction ID
        extra: Optional additional data

    Returns:
        32-character hex idempotency key
    """
    components = [provider, transaction_id]
    if extra:
        components.append(extra)

    data = ":".join(components)
    key = hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]

    logger.debug(
        "Generated idempotency key",
        provider=provider,
        transaction_id=transaction_id,
    )

    return key


def generate_payment_idempotency_key(
    customer_id: str,
    amount: float,
    currency: str,
    timestamp: Optional[datetime] = None,
) -> str:
    """
    Generate an idempotency key for a payment request.

    Uses customer, amount, currency, and hour to prevent
    duplicate payments within the same hour.

    Args:
        customer_id: Customer ID
        amount: Payment amount
        currency: Currency code
        timestamp: Optional timestamp (defaults to now)

    Returns:
        Idempotency key
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    # Truncate to hour to allow same payment after 1 hour
    hour_key = timestamp.strftime("%Y%m%d%H")

    data = f"{customer_id}:{amount:.2f}:{currency}:{hour_key}"
    key = hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]

    logger.debug(
        "Generated payment idempotency key",
        customer_id=customer_id,
        amount=amount,
        currency=currency,
    )

    return key
