"""
Authentication Service for PAYGO Middleware

Provides comprehensive authentication operations:
- User login/logout
- JWT token generation and validation
- Refresh token management
- Password operations
- Rate limiting for failed attempts

CRITICAL: All authentication events are logged LOUDLY.
Failed attempts are tracked and rate-limited.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
)
from app.core.logging import audit_logger, get_logger
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.refresh_token import (
    RefreshToken,
    find_refresh_token_async,
    hash_token,
    revoke_all_user_tokens,
)
from app.models.user import User, UserRole

logger = get_logger(__name__)


# Password complexity requirements
MIN_PASSWORD_LENGTH = 8
PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"  # At least 1 lowercase, 1 uppercase, 1 digit
)


# Rate limiting for failed login attempts
# In production, use Redis for distributed rate limiting
_failed_attempts: dict[str, list[datetime]] = defaultdict(list)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15


class LoginResult:
    """Result of a successful login operation."""

    def __init__(
        self,
        user: User,
        access_token: str,
        refresh_token: str,
        access_token_expires_at: datetime,
        refresh_token_expires_at: datetime,
    ):
        self.user = user
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.access_token_expires_at = access_token_expires_at
        self.refresh_token_expires_at = refresh_token_expires_at

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": "bearer",
            "expires_in": int(
                (self.access_token_expires_at - datetime.now(timezone.utc)).total_seconds()
            ),
            "user": self.user.to_dict_safe(),
        }


class TokenRefreshResult:
    """Result of a token refresh operation."""

    def __init__(
        self,
        access_token: str,
        refresh_token: Optional[str],
        access_token_expires_at: datetime,
        refresh_token_expires_at: Optional[datetime],
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.access_token_expires_at = access_token_expires_at
        self.refresh_token_expires_at = refresh_token_expires_at

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        result = {
            "access_token": self.access_token,
            "token_type": "bearer",
            "expires_in": int(
                (self.access_token_expires_at - datetime.now(timezone.utc)).total_seconds()
            ),
        }
        if self.refresh_token:
            result["refresh_token"] = self.refresh_token
        return result


class AuthService:
    """
    Authentication service handling login, logout, and token operations.

    LOUD: All authentication events are logged with context.
    """

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    # =========================================================================
    # Rate Limiting
    # =========================================================================

    def _check_rate_limit(self, identifier: str) -> None:
        """
        Check if identifier (username/IP) is rate-limited.

        LOUD: Logs rate limit checks and violations.

        Args:
            identifier: Username or IP to check

        Raises:
            RateLimitError: If too many failed attempts
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=LOCKOUT_DURATION_MINUTES)

        # Clean old attempts
        _failed_attempts[identifier] = [
            t for t in _failed_attempts[identifier] if t > cutoff
        ]

        # Check count
        attempt_count = len(_failed_attempts[identifier])

        if attempt_count >= MAX_FAILED_ATTEMPTS:
            lockout_remaining = (
                _failed_attempts[identifier][0]
                + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                - now
            )

            logger.warning(
                "Login rate limit exceeded",
                identifier=identifier,
                attempts=attempt_count,
                lockout_seconds=int(lockout_remaining.total_seconds()),
            )

            raise RateLimitError(
                limit=MAX_FAILED_ATTEMPTS,
                window=f"{LOCKOUT_DURATION_MINUTES} minutes",
                context={
                    "identifier": identifier,
                    "retry_after_seconds": int(lockout_remaining.total_seconds()),
                },
            )

    def _record_failed_attempt(self, identifier: str) -> None:
        """Record a failed login attempt."""
        _failed_attempts[identifier].append(datetime.now(timezone.utc))
        logger.warning(
            "Failed login attempt recorded",
            identifier=identifier,
            total_attempts=len(_failed_attempts[identifier]),
        )

    def _clear_failed_attempts(self, identifier: str) -> None:
        """Clear failed attempts after successful login."""
        if identifier in _failed_attempts:
            del _failed_attempts[identifier]

    # =========================================================================
    # Login
    # =========================================================================

    async def login(
        self,
        username: str,
        password: str,
        device_info: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> LoginResult:
        """
        Authenticate user and generate tokens.

        LOUD: Logs all login attempts with outcome.

        Args:
            username: Username or email
            password: Plain text password
            device_info: User agent string
            ip_address: Client IP address

        Returns:
            LoginResult with tokens and user info

        Raises:
            AuthenticationError: If credentials are invalid
            RateLimitError: If too many failed attempts
        """
        # Rate limit check
        self._check_rate_limit(username)
        if ip_address:
            self._check_rate_limit(ip_address)

        logger.info(
            "Login attempt",
            username=username,
            ip_address=ip_address,
        )

        # Find user by username or email
        result = await self.db.execute(
            select(User).where(
                (User.username == username.lower()) | (User.email == username.lower())
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            self._record_failed_attempt(username)
            if ip_address:
                self._record_failed_attempt(ip_address)

            logger.warning(
                "Login failed - user not found",
                username=username,
                ip_address=ip_address,
            )

            audit_logger.log_event(
                event_type="auth",
                action="login",
                outcome="failure",
                details={"username": username, "reason": "user_not_found"},
            )

            raise AuthenticationError(
                "Invalid credentials",
                context={"username": username},
            )

        # Check password
        if not verify_password(password, user.hashed_password):
            self._record_failed_attempt(username)
            if ip_address:
                self._record_failed_attempt(ip_address)

            logger.warning(
                "Login failed - invalid password",
                user_id=str(user.id),
                username=username,
                ip_address=ip_address,
            )

            audit_logger.log_event(
                event_type="auth",
                action="login",
                outcome="failure",
                resource_type="user",
                resource_id=str(user.id),
                details={"reason": "invalid_password"},
            )

            raise AuthenticationError(
                "Invalid credentials",
                context={"username": username},
            )

        # Check if user is active
        if not user.is_active:
            logger.warning(
                "Login failed - user inactive",
                user_id=str(user.id),
                username=username,
            )

            audit_logger.log_event(
                event_type="auth",
                action="login",
                outcome="failure",
                resource_type="user",
                resource_id=str(user.id),
                details={"reason": "user_inactive"},
            )

            raise AuthenticationError(
                "Account is deactivated",
                context={"username": username},
            )

        # Clear failed attempts
        self._clear_failed_attempts(username)
        if ip_address:
            self._clear_failed_attempts(ip_address)

        # Generate access token
        access_token_expires = timedelta(minutes=settings.jwt_expiration_minutes)
        access_token = create_access_token(
            subject=str(user.id),
            expires_delta=access_token_expires,
            extra_claims={
                "username": user.username,
                "role": user.role.value,
            },
        )
        access_token_expires_at = datetime.now(timezone.utc) + access_token_expires

        # Generate refresh token
        refresh_token_obj, refresh_token_plain = RefreshToken.create_for_user(
            user_id=user.id,
            device_info=device_info,
            ip_address=ip_address,
        )

        # Save refresh token
        self.db.add(refresh_token_obj)

        # Update last login
        user.update_last_login()

        await self.db.commit()

        logger.info(
            "Login successful",
            user_id=str(user.id),
            username=username,
            role=user.role.value,
            ip_address=ip_address,
        )

        audit_logger.log_event(
            event_type="auth",
            action="login",
            outcome="success",
            resource_type="user",
            resource_id=str(user.id),
            details={
                "username": user.username,
                "role": user.role.value,
                "ip_address": ip_address,
            },
        )

        return LoginResult(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token_plain,
            access_token_expires_at=access_token_expires_at,
            refresh_token_expires_at=refresh_token_obj.expires_at,
        )

    # =========================================================================
    # Logout
    # =========================================================================

    async def logout(
        self,
        refresh_token: str,
        user_id: Optional[UUID] = None,
    ) -> bool:
        """
        Logout by revoking refresh token.

        LOUD: Logs logout events.

        Args:
            refresh_token: Refresh token to revoke
            user_id: Optional user ID for verification

        Returns:
            True if token was revoked, False if not found
        """
        token_obj = await find_refresh_token_async(self.db, refresh_token)

        if not token_obj:
            logger.warning("Logout failed - token not found")
            return False

        # Verify user if provided
        if user_id and token_obj.user_id != user_id:
            logger.warning(
                "Logout failed - user mismatch",
                token_user_id=str(token_obj.user_id),
                provided_user_id=str(user_id),
            )
            return False

        token_obj.revoke(reason="user_logout")
        await self.db.commit()

        logger.info(
            "Logout successful",
            user_id=str(token_obj.user_id),
        )

        audit_logger.log_event(
            event_type="auth",
            action="logout",
            outcome="success",
            resource_type="user",
            resource_id=str(token_obj.user_id),
        )

        return True

    async def logout_all(self, user_id: UUID) -> int:
        """
        Logout from all sessions by revoking all refresh tokens.

        LOUD: Logs mass logout.

        Args:
            user_id: User's UUID

        Returns:
            Number of tokens revoked
        """
        count = await revoke_all_user_tokens(
            self.db, user_id, reason="logout_all_sessions"
        )
        await self.db.commit()

        logger.info(
            "Logout all sessions successful",
            user_id=str(user_id),
            sessions_revoked=count,
        )

        return count

    # =========================================================================
    # Token Refresh
    # =========================================================================

    async def refresh_tokens(
        self,
        refresh_token: str,
        rotate_refresh_token: bool = True,
    ) -> TokenRefreshResult:
        """
        Refresh access token using refresh token.

        LOUD: Logs all refresh attempts.

        Args:
            refresh_token: Current refresh token
            rotate_refresh_token: Whether to issue new refresh token

        Returns:
            TokenRefreshResult with new tokens

        Raises:
            AuthenticationError: If refresh token is invalid
        """
        logger.debug("Token refresh attempt")

        # Find refresh token
        token_obj = await find_refresh_token_async(self.db, refresh_token)

        if not token_obj:
            logger.warning("Token refresh failed - token not found")
            raise AuthenticationError(
                "Invalid refresh token",
                context={"reason": "not_found"},
            )

        # Check if valid
        if not token_obj.is_valid():
            if token_obj.is_revoked:
                reason = "revoked"
            elif token_obj.is_expired():
                reason = "expired"
            else:
                reason = "invalid"

            logger.warning(
                "Token refresh failed - token invalid",
                reason=reason,
                user_id=str(token_obj.user_id),
            )

            raise AuthenticationError(
                f"Refresh token is {reason}",
                context={"reason": reason},
            )

        # Get user
        result = await self.db.execute(
            select(User).where(User.id == token_obj.user_id)
        )
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            logger.warning(
                "Token refresh failed - user invalid",
                user_id=str(token_obj.user_id),
                user_active=user.is_active if user else None,
            )
            raise AuthenticationError(
                "User account is not available",
                context={"reason": "user_unavailable"},
            )

        # Generate new access token
        access_token_expires = timedelta(minutes=settings.jwt_expiration_minutes)
        access_token = create_access_token(
            subject=str(user.id),
            expires_delta=access_token_expires,
            extra_claims={
                "username": user.username,
                "role": user.role.value,
            },
        )
        access_token_expires_at = datetime.now(timezone.utc) + access_token_expires

        # Optionally rotate refresh token
        new_refresh_token = None
        new_refresh_expires_at = None

        if rotate_refresh_token:
            # Revoke old token
            token_obj.revoke(reason="token_rotation")

            # Create new refresh token
            new_token_obj, new_refresh_token = RefreshToken.create_for_user(
                user_id=user.id,
                device_info=token_obj.device_info,
                ip_address=token_obj.ip_address,
            )
            self.db.add(new_token_obj)
            new_refresh_expires_at = new_token_obj.expires_at

        await self.db.commit()

        logger.info(
            "Token refresh successful",
            user_id=str(user.id),
            rotated=rotate_refresh_token,
        )

        return TokenRefreshResult(
            access_token=access_token,
            refresh_token=new_refresh_token,
            access_token_expires_at=access_token_expires_at,
            refresh_token_expires_at=new_refresh_expires_at,
        )

    # =========================================================================
    # Password Operations
    # =========================================================================

    async def change_password(
        self,
        user_id: UUID,
        current_password: str,
        new_password: str,
        revoke_sessions: bool = True,
    ) -> bool:
        """
        Change user's password.

        LOUD: Logs password change events.

        Args:
            user_id: User's UUID
            current_password: Current password for verification
            new_password: New password
            revoke_sessions: Whether to revoke all refresh tokens

        Returns:
            True if password was changed

        Raises:
            AuthenticationError: If current password is wrong
            ValueError: If new password is invalid
        """
        logger.info("Password change attempt", user_id=str(user_id))

        # Get user
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            logger.error("Password change failed - user not found", user_id=str(user_id))
            raise AuthenticationError("User not found")

        # Verify current password
        if not verify_password(current_password, user.hashed_password):
            logger.warning(
                "Password change failed - wrong current password",
                user_id=str(user_id),
            )

            audit_logger.log_event(
                event_type="auth",
                action="password_change",
                outcome="failure",
                resource_type="user",
                resource_id=str(user_id),
                details={"reason": "wrong_current_password"},
            )

            raise AuthenticationError("Current password is incorrect")

        # Validate new password
        self._validate_password(new_password)

        # Update password
        user.hashed_password = hash_password(new_password)

        # Optionally revoke all sessions
        if revoke_sessions:
            await revoke_all_user_tokens(self.db, user_id, reason="password_change")

        await self.db.commit()

        logger.info(
            "Password changed successfully",
            user_id=str(user_id),
            sessions_revoked=revoke_sessions,
        )

        audit_logger.log_event(
            event_type="auth",
            action="password_change",
            outcome="success",
            resource_type="user",
            resource_id=str(user_id),
            details={"sessions_revoked": revoke_sessions},
        )

        return True

    async def reset_password_admin(
        self,
        user_id: UUID,
        new_password: str,
        admin_id: UUID,
    ) -> bool:
        """
        Reset user's password (admin operation).

        LOUD: Logs admin password reset.

        Args:
            user_id: Target user's UUID
            new_password: New password
            admin_id: Admin performing the reset

        Returns:
            True if password was reset
        """
        logger.info(
            "Admin password reset",
            user_id=str(user_id),
            admin_id=str(admin_id),
        )

        # Get user
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            logger.error("Password reset failed - user not found", user_id=str(user_id))
            raise AuthenticationError("User not found")

        # Validate new password
        self._validate_password(new_password)

        # Update password
        user.hashed_password = hash_password(new_password)

        # Revoke all sessions
        await revoke_all_user_tokens(self.db, user_id, reason="admin_password_reset")

        await self.db.commit()

        logger.warning(
            "Admin password reset completed",
            user_id=str(user_id),
            admin_id=str(admin_id),
        )

        audit_logger.log_event(
            event_type="auth",
            action="password_reset_admin",
            outcome="success",
            resource_type="user",
            resource_id=str(user_id),
            details={"admin_id": str(admin_id)},
        )

        return True

    def _validate_password(self, password: str) -> None:
        """
        Validate password meets requirements.

        Requirements:
        - Minimum 8 characters
        - At least 1 lowercase letter
        - At least 1 uppercase letter
        - At least 1 digit

        Args:
            password: Password to validate

        Raises:
            ValueError: If password doesn't meet requirements
        """
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )

        if not PASSWORD_PATTERN.match(password):
            raise ValueError(
                "Password must contain at least one lowercase letter, "
                "one uppercase letter, and one digit"
            )

    # =========================================================================
    # User Lookup
    # =========================================================================

    async def get_user_by_id(self, user_id: UUID) -> Optional[User]:
        """Get user by ID."""
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        result = await self.db.execute(
            select(User).where(User.username == username.lower())
        )
        return result.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        result = await self.db.execute(
            select(User).where(User.email == email.lower())
        )
        return result.scalar_one_or_none()
