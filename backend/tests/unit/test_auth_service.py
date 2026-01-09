"""
Unit Tests for Authentication Service

Tests:
- Login (success, invalid credentials, rate limiting)
- Logout (single session, all sessions)
- Token refresh
- Password change
- Password validation
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.user import User, UserRole
from app.models.refresh_token import RefreshToken, hash_token
from app.services.auth_service import (
    AuthService,
    LoginResult,
    MIN_PASSWORD_LENGTH,
    PASSWORD_PATTERN,
    _failed_attempts,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def mock_user():
    """Create a mock user."""
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.username = "testuser"
    user.email = "test@example.com"
    user.hashed_password = "$2b$12$test_hash"  # Mock bcrypt hash
    user.full_name = "Test User"
    user.role = UserRole.ADMIN
    user.is_active = True
    user.last_login_at = None
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    user.update_last_login = MagicMock()
    user.to_dict_safe = MagicMock(return_value={
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
    })
    return user


@pytest.fixture
def mock_refresh_token():
    """Create a mock refresh token."""
    token = MagicMock(spec=RefreshToken)
    token.id = uuid4()
    token.user_id = uuid4()
    token.token_hash = hash_token("test_token")
    token.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    token.is_revoked = False
    token.revoked_at = None
    token.device_info = "Test Browser"
    token.ip_address = "127.0.0.1"
    token.is_valid = MagicMock(return_value=True)
    token.is_expired = MagicMock(return_value=False)
    token.revoke = MagicMock()
    return token


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit tracking before each test."""
    _failed_attempts.clear()
    yield
    _failed_attempts.clear()


# =============================================================================
# Password Validation Tests
# =============================================================================


class TestPasswordValidation:
    """Test password validation logic."""

    def test_password_minimum_length(self, mock_db):
        """Test password minimum length requirement."""
        auth_service = AuthService(mock_db)

        # Too short
        with pytest.raises(ValueError, match="at least 8 characters"):
            auth_service._validate_password("Short1")

        # Exactly minimum length
        auth_service._validate_password("Validpw1")  # Should not raise

    def test_password_requires_lowercase(self, mock_db):
        """Test password requires lowercase letter."""
        auth_service = AuthService(mock_db)

        with pytest.raises(ValueError, match="lowercase"):
            auth_service._validate_password("ALLUPPER123")

    def test_password_requires_uppercase(self, mock_db):
        """Test password requires uppercase letter."""
        auth_service = AuthService(mock_db)

        with pytest.raises(ValueError, match="uppercase"):
            auth_service._validate_password("alllower123")

    def test_password_requires_digit(self, mock_db):
        """Test password requires digit."""
        auth_service = AuthService(mock_db)

        with pytest.raises(ValueError, match="digit"):
            auth_service._validate_password("NoDigitsHere")

    def test_valid_password(self, mock_db):
        """Test valid password passes validation."""
        auth_service = AuthService(mock_db)

        # All requirements met
        auth_service._validate_password("ValidPassword123")
        auth_service._validate_password("Another1Valid")
        auth_service._validate_password("P@ssw0rd!")


# =============================================================================
# Login Tests
# =============================================================================


class TestLogin:
    """Test login functionality."""

    @pytest.mark.asyncio
    async def test_login_success(self, mock_db, mock_user):
        """Test successful login."""
        # Setup mock
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)

        with patch("app.services.auth_service.verify_password", return_value=True):
            with patch("app.services.auth_service.create_access_token", return_value="access_token"):
                with patch.object(
                    RefreshToken,
                    "create_for_user",
                    return_value=(MagicMock(expires_at=datetime.now(timezone.utc) + timedelta(days=7)), "refresh_token"),
                ):
                    result = await auth_service.login(
                        username="testuser",
                        password="ValidPassword123",
                    )

        assert isinstance(result, LoginResult)
        assert result.access_token == "access_token"
        assert result.refresh_token == "refresh_token"
        assert result.user == mock_user
        mock_user.update_last_login.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_login_user_not_found(self, mock_db):
        """Test login with non-existent user."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)

        from app.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError, match="Invalid credentials"):
            await auth_service.login(
                username="nonexistent",
                password="password",
            )

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, mock_db, mock_user):
        """Test login with wrong password."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)

        with patch("app.services.auth_service.verify_password", return_value=False):
            from app.core.exceptions import AuthenticationError

            with pytest.raises(AuthenticationError, match="Invalid credentials"):
                await auth_service.login(
                    username="testuser",
                    password="wrongpassword",
                )

    @pytest.mark.asyncio
    async def test_login_inactive_user(self, mock_db, mock_user):
        """Test login with inactive user."""
        mock_user.is_active = False
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)

        with patch("app.services.auth_service.verify_password", return_value=True):
            from app.core.exceptions import AuthenticationError

            with pytest.raises(AuthenticationError, match="deactivated"):
                await auth_service.login(
                    username="testuser",
                    password="password",
                )

    @pytest.mark.asyncio
    async def test_login_rate_limiting(self, mock_db):
        """Test login rate limiting after failed attempts."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)

        from app.core.exceptions import AuthenticationError, RateLimitError

        # Make 5 failed attempts
        for i in range(5):
            with pytest.raises(AuthenticationError):
                await auth_service.login(
                    username="ratelimited_user",
                    password="wrong",
                )

        # 6th attempt should be rate limited
        with pytest.raises(RateLimitError):
            await auth_service.login(
                username="ratelimited_user",
                password="wrong",
            )


# =============================================================================
# Logout Tests
# =============================================================================


class TestLogout:
    """Test logout functionality."""

    @pytest.mark.asyncio
    async def test_logout_success(self, mock_db, mock_refresh_token):
        """Test successful logout."""
        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=mock_refresh_token,
        ):
            auth_service = AuthService(mock_db)
            result = await auth_service.logout(
                refresh_token="test_token",
                user_id=mock_refresh_token.user_id,
            )

        assert result is True
        mock_refresh_token.revoke.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_token_not_found(self, mock_db):
        """Test logout with non-existent token."""
        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=None,
        ):
            auth_service = AuthService(mock_db)
            result = await auth_service.logout(refresh_token="invalid_token")

        assert result is False

    @pytest.mark.asyncio
    async def test_logout_user_mismatch(self, mock_db, mock_refresh_token):
        """Test logout with mismatched user ID."""
        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=mock_refresh_token,
        ):
            auth_service = AuthService(mock_db)
            result = await auth_service.logout(
                refresh_token="test_token",
                user_id=uuid4(),  # Different user
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_logout_all_sessions(self, mock_db):
        """Test logout from all sessions."""
        user_id = uuid4()

        with patch(
            "app.services.auth_service.revoke_all_user_tokens",
            return_value=3,
        ) as mock_revoke:
            auth_service = AuthService(mock_db)
            count = await auth_service.logout_all(user_id)

        assert count == 3
        mock_revoke.assert_called_once_with(mock_db, user_id, reason="logout_all_sessions")


# =============================================================================
# Token Refresh Tests
# =============================================================================


class TestTokenRefresh:
    """Test token refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_success(self, mock_db, mock_user, mock_refresh_token):
        """Test successful token refresh."""
        mock_refresh_token.user_id = mock_user.id

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=mock_refresh_token,
        ):
            with patch("app.services.auth_service.create_access_token", return_value="new_access_token"):
                with patch.object(
                    RefreshToken,
                    "create_for_user",
                    return_value=(
                        MagicMock(expires_at=datetime.now(timezone.utc) + timedelta(days=7)),
                        "new_refresh_token",
                    ),
                ):
                    auth_service = AuthService(mock_db)
                    result = await auth_service.refresh_tokens(
                        refresh_token="test_token",
                        rotate_refresh_token=True,
                    )

        assert result.access_token == "new_access_token"
        assert result.refresh_token == "new_refresh_token"
        mock_refresh_token.revoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_invalid_token(self, mock_db):
        """Test refresh with invalid token."""
        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=None,
        ):
            auth_service = AuthService(mock_db)

            from app.core.exceptions import AuthenticationError

            with pytest.raises(AuthenticationError, match="Invalid refresh token"):
                await auth_service.refresh_tokens(refresh_token="invalid_token")

    @pytest.mark.asyncio
    async def test_refresh_revoked_token(self, mock_db, mock_refresh_token):
        """Test refresh with revoked token."""
        mock_refresh_token.is_valid.return_value = False
        mock_refresh_token.is_revoked = True

        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=mock_refresh_token,
        ):
            auth_service = AuthService(mock_db)

            from app.core.exceptions import AuthenticationError

            with pytest.raises(AuthenticationError, match="revoked"):
                await auth_service.refresh_tokens(refresh_token="test_token")

    @pytest.mark.asyncio
    async def test_refresh_expired_token(self, mock_db, mock_refresh_token):
        """Test refresh with expired token."""
        mock_refresh_token.is_valid.return_value = False
        mock_refresh_token.is_expired.return_value = True
        mock_refresh_token.is_revoked = False

        with patch(
            "app.services.auth_service.find_refresh_token_async",
            return_value=mock_refresh_token,
        ):
            auth_service = AuthService(mock_db)

            from app.core.exceptions import AuthenticationError

            with pytest.raises(AuthenticationError, match="expired"):
                await auth_service.refresh_tokens(refresh_token="test_token")


# =============================================================================
# Password Change Tests
# =============================================================================


class TestPasswordChange:
    """Test password change functionality."""

    @pytest.mark.asyncio
    async def test_change_password_success(self, mock_db, mock_user):
        """Test successful password change."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        with patch("app.services.auth_service.verify_password", return_value=True):
            with patch("app.services.auth_service.hash_password", return_value="new_hash"):
                with patch(
                    "app.services.auth_service.revoke_all_user_tokens",
                    return_value=2,
                ):
                    auth_service = AuthService(mock_db)
                    result = await auth_service.change_password(
                        user_id=mock_user.id,
                        current_password="OldPassword123",
                        new_password="NewPassword123",
                    )

        assert result is True
        assert mock_user.hashed_password == "new_hash"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, mock_db, mock_user):
        """Test password change with wrong current password."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        with patch("app.services.auth_service.verify_password", return_value=False):
            auth_service = AuthService(mock_db)

            from app.core.exceptions import AuthenticationError

            with pytest.raises(AuthenticationError, match="incorrect"):
                await auth_service.change_password(
                    user_id=mock_user.id,
                    current_password="WrongPassword",
                    new_password="NewPassword123",
                )

    @pytest.mark.asyncio
    async def test_change_password_invalid_new(self, mock_db, mock_user):
        """Test password change with invalid new password."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        with patch("app.services.auth_service.verify_password", return_value=True):
            auth_service = AuthService(mock_db)

            with pytest.raises(ValueError, match="at least 8 characters"):
                await auth_service.change_password(
                    user_id=mock_user.id,
                    current_password="OldPassword123",
                    new_password="short",
                )


# =============================================================================
# User Lookup Tests
# =============================================================================


class TestUserLookup:
    """Test user lookup methods."""

    @pytest.mark.asyncio
    async def test_get_user_by_id(self, mock_db, mock_user):
        """Test getting user by ID."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)
        result = await auth_service.get_user_by_id(mock_user.id)

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_user_by_username(self, mock_db, mock_user):
        """Test getting user by username."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)
        result = await auth_service.get_user_by_username("testuser")

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, mock_db, mock_user):
        """Test getting user by email."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)
        result = await auth_service.get_user_by_email("test@example.com")

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, mock_db):
        """Test getting non-existent user."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        auth_service = AuthService(mock_db)
        result = await auth_service.get_user_by_id(uuid4())

        assert result is None


# =============================================================================
# LoginResult Tests
# =============================================================================


class TestLoginResult:
    """Test LoginResult class."""

    def test_to_dict(self, mock_user):
        """Test LoginResult.to_dict()."""
        result = LoginResult(
            user=mock_user,
            access_token="access_token",
            refresh_token="refresh_token",
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            refresh_token_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )

        data = result.to_dict()

        assert data["access_token"] == "access_token"
        assert data["refresh_token"] == "refresh_token"
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
        assert data["expires_in"] > 0
        assert "user" in data
