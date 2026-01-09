"""
RefreshToken Model for PAYGO Middleware

Stores refresh tokens for JWT authentication with revocation support.

CRITICAL: Refresh tokens are hashed before storage for security.
All token operations are logged LOUDLY.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.logging import audit_logger, get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User

logger = get_logger(__name__)


# Default refresh token expiry: 7 days
DEFAULT_REFRESH_TOKEN_EXPIRY_DAYS = 7


def hash_token(token: str) -> str:
    """
    Hash a token for secure storage.

    Uses SHA-256 for fast, secure hashing.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    """
    Generate a cryptographically secure refresh token.

    Returns:
        64-character URL-safe token
    """
    return secrets.token_urlsafe(48)


class RefreshToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    RefreshToken model for managing JWT refresh tokens.

    Features:
    - Tokens are hashed before storage
    - Each token can be individually revoked
    - Tracks device/user agent for session management
    - Automatic expiry

    Attributes:
        id: Unique identifier (UUID)
        user_id: Associated user
        token_hash: SHA-256 hash of the token
        expires_at: Expiration timestamp
        is_revoked: Whether token has been revoked
        revoked_at: When token was revoked
        device_info: User agent / device information
        ip_address: IP address used for login
    """

    __tablename__ = "refresh_token"

    # User relationship
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Token hash (not the actual token)
    token_hash: Mapped[str] = mapped_column(
        String(64),  # SHA-256 produces 64 hex characters
        unique=True,
        index=True,
        nullable=False,
    )

    # Expiration
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Revocation status
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Session tracking
    device_info: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),  # IPv6 max length
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="refresh_tokens",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        Index("ix_refresh_token_user_active", "user_id", "is_revoked"),
        Index("ix_refresh_token_expires_revoked", "expires_at", "is_revoked"),
    )

    # ==========================================================================
    # Class Methods
    # ==========================================================================

    @classmethod
    def create_for_user(
        cls,
        user_id: UUID,
        device_info: Optional[str] = None,
        ip_address: Optional[str] = None,
        expiry_days: int = DEFAULT_REFRESH_TOKEN_EXPIRY_DAYS,
    ) -> tuple["RefreshToken", str]:
        """
        Create a new refresh token for a user.

        Args:
            user_id: User's UUID
            device_info: User agent / device information
            ip_address: Client IP address
            expiry_days: Days until expiry

        Returns:
            Tuple of (RefreshToken instance, plain token string)

        LOUD: Logs token creation.
        """
        # Generate plain token
        plain_token = generate_refresh_token()

        # Calculate expiry
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)

        # Create instance with hashed token
        token_instance = cls(
            user_id=user_id,
            token_hash=hash_token(plain_token),
            expires_at=expires_at,
            device_info=device_info,
            ip_address=ip_address,
        )

        logger.info(
            "Refresh token created",
            user_id=str(user_id),
            expires_at=expires_at.isoformat(),
            device_info=device_info[:50] if device_info else None,
        )

        return token_instance, plain_token

    @classmethod
    def find_by_token(cls, db_session, token: str) -> Optional["RefreshToken"]:
        """
        Find a refresh token by its plain text value.

        Note: This is synchronous - use with caution in async contexts.

        Args:
            db_session: Database session
            token: Plain text token

        Returns:
            RefreshToken if found, None otherwise
        """
        from sqlalchemy import select

        token_hash = hash_token(token)
        result = db_session.execute(
            select(cls).where(cls.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    # ==========================================================================
    # Instance Methods
    # ==========================================================================

    def is_valid(self) -> bool:
        """
        Check if token is still valid.

        Returns:
            True if token is not revoked and not expired
        """
        if self.is_revoked:
            return False

        if datetime.now(timezone.utc) >= self.expires_at:
            return False

        return True

    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.now(timezone.utc) >= self.expires_at

    def revoke(self, reason: str = "") -> None:
        """
        Revoke this refresh token.

        LOUD: Logs revocation with reason.

        Args:
            reason: Reason for revocation
        """
        if self.is_revoked:
            logger.warning(
                "Attempted to revoke already revoked token",
                token_id=str(self.id),
            )
            return

        self.is_revoked = True
        self.revoked_at = datetime.now(timezone.utc)

        logger.info(
            "Refresh token revoked",
            token_id=str(self.id),
            user_id=str(self.user_id),
            reason=reason,
        )

        audit_logger.log_event(
            event_type="auth",
            action="refresh_token_revoke",
            resource_type="refresh_token",
            resource_id=str(self.id),
            details={
                "user_id": str(self.user_id),
                "reason": reason,
            },
        )

    def time_until_expiry(self) -> timedelta:
        """Get time remaining until expiry."""
        now = datetime.now(timezone.utc)
        if now >= self.expires_at:
            return timedelta(0)
        return self.expires_at - now

    def to_dict_safe(self) -> dict:
        """
        Convert to dictionary without sensitive fields.

        NEVER includes token hash.
        """
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "expires_at": self.expires_at.isoformat(),
            "is_revoked": self.is_revoked,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "device_info": self.device_info,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Utility function for async context
async def find_refresh_token_async(db_session, token: str) -> Optional[RefreshToken]:
    """
    Find a refresh token by its plain text value (async version).

    Args:
        db_session: Async database session
        token: Plain text token

    Returns:
        RefreshToken if found, None otherwise
    """
    from sqlalchemy import select

    token_hash = hash_token(token)
    result = await db_session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def revoke_all_user_tokens(db_session, user_id: UUID, reason: str = "") -> int:
    """
    Revoke all refresh tokens for a user.

    LOUD: Logs mass revocation.

    Args:
        db_session: Async database session
        user_id: User's UUID
        reason: Reason for revocation

    Returns:
        Number of tokens revoked
    """
    from sqlalchemy import select, update

    now = datetime.now(timezone.utc)

    # Count active tokens
    count_result = await db_session.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked == False,  # noqa: E712
        )
    )
    tokens = count_result.scalars().all()
    count = len(tokens)

    if count > 0:
        # Revoke all
        await db_session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.is_revoked == False,  # noqa: E712
            )
            .values(is_revoked=True, revoked_at=now)
        )

        logger.warning(
            "All user refresh tokens revoked",
            user_id=str(user_id),
            count=count,
            reason=reason,
        )

        audit_logger.log_event(
            event_type="auth",
            action="revoke_all_tokens",
            resource_type="user",
            resource_id=str(user_id),
            details={"count": count, "reason": reason},
        )

    return count


async def cleanup_expired_tokens(db_session) -> int:
    """
    Remove expired refresh tokens from database.

    Should be run periodically via scheduler.

    Args:
        db_session: Async database session

    Returns:
        Number of tokens deleted
    """
    from sqlalchemy import delete, select

    now = datetime.now(timezone.utc)

    # Count expired tokens
    count_result = await db_session.execute(
        select(RefreshToken).where(RefreshToken.expires_at < now)
    )
    count = len(count_result.scalars().all())

    if count > 0:
        # Delete expired tokens
        await db_session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < now)
        )

        logger.info(
            "Expired refresh tokens cleaned up",
            count=count,
        )

    return count
