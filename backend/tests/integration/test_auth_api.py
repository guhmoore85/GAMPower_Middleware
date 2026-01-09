"""
Integration Tests for Authentication API Endpoints

Tests all auth-related API endpoints:
- POST /auth/login - Login and get tokens
- POST /auth/logout - Logout current session
- POST /auth/logout-all - Logout all sessions
- POST /auth/refresh - Refresh access token
- GET /auth/me - Get current user info
- POST /auth/change-password - Change password
- GET /auth/sessions - List active sessions
- DELETE /auth/sessions/{id} - Revoke specific session

CRITICAL: Each test includes clear assertion messages.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_password():
    """Standard test password."""
    return "TestPassword123"


@pytest.fixture
def weak_password():
    """Password that fails validation."""
    return "weak"


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession, test_password: str) -> User:
    """Create a test user in the database."""
    user = User(
        username=f"testuser_{uuid4().hex[:8]}",
        email=f"test_{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(test_password),
        full_name="Test User",
        role=UserRole.TECHNICIAN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, test_password: str) -> User:
    """Create an admin user in the database."""
    user = User(
        username=f"admin_{uuid4().hex[:8]}",
        email=f"admin_{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(test_password),
        full_name="Admin User",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def inactive_user(db_session: AsyncSession, test_password: str) -> User:
    """Create an inactive user in the database."""
    user = User(
        username=f"inactive_{uuid4().hex[:8]}",
        email=f"inactive_{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(test_password),
        full_name="Inactive User",
        role=UserRole.READONLY,
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


async def login_user(client: AsyncClient, username: str, password: str) -> dict:
    """Helper to login and get tokens."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()


# =============================================================================
# Login Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
class TestLoginEndpoint:
    """Tests for POST /auth/login endpoint."""

    async def test_login_success_with_username(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test successful login with username."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": test_user.username,
                "password": test_password,
            },
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "access_token" in data, "Response must contain access_token"
        assert "refresh_token" in data, "Response must contain refresh_token"
        assert "expires_in" in data, "Response must contain expires_in"
        assert "user" in data, "Response must contain user info"
        assert data["token_type"] == "bearer", "Token type must be bearer"
        assert data["user"]["username"] == test_user.username

    async def test_login_success_with_email(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test successful login with email."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": test_user.email,  # Using email as username
                "password": test_password,
            },
        )

        assert response.status_code == 200
        assert response.json()["user"]["email"] == test_user.email

    async def test_login_wrong_password(
        self, client: AsyncClient, test_user: User
    ):
        """Test login with wrong password."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": test_user.username,
                "password": "WrongPassword123",
            },
        )

        assert response.status_code == 401, "Wrong password should return 401"

        data = response.json()
        assert data["detail"]["error_code"] == "AUTHENTICATION_ERROR"

    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Test login with non-existent user."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "nonexistent_user",
                "password": "SomePassword123",
            },
        )

        assert response.status_code == 401

    async def test_login_inactive_user(
        self, client: AsyncClient, inactive_user: User, test_password: str
    ):
        """Test login with inactive user account."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": inactive_user.username,
                "password": test_password,
            },
        )

        assert response.status_code == 401
        assert "deactivated" in response.json()["detail"]["message"].lower()

    async def test_login_returns_user_role(
        self, client: AsyncClient, admin_user: User, test_password: str
    ):
        """Test that login returns correct user role."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": admin_user.username,
                "password": test_password,
            },
        )

        assert response.status_code == 200
        assert response.json()["user"]["role"] == "admin"

    async def test_login_rate_limiting(self, client: AsyncClient, test_user: User):
        """Test rate limiting after multiple failed attempts."""
        # Make 5 failed attempts
        for i in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={
                    "username": test_user.username,
                    "password": "WrongPassword",
                },
            )

        # 6th attempt should be rate limited
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": test_user.username,
                "password": "WrongPassword",
            },
        )

        assert response.status_code == 429, "Should be rate limited after 5 failed attempts"
        assert "Retry-After" in response.headers


# =============================================================================
# Logout Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
class TestLogoutEndpoint:
    """Tests for POST /auth/logout endpoint."""

    async def test_logout_success(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test successful logout."""
        # Login first
        login_data = await login_user(client, test_user.username, test_password)
        access_token = login_data["access_token"]
        refresh_token = login_data["refresh_token"]

        # Logout
        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200, f"Logout failed: {response.text}"
        assert response.json()["success"] is True

    async def test_logout_invalid_refresh_token(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test logout with invalid refresh token."""
        login_data = await login_user(client, test_user.username, test_password)
        access_token = login_data["access_token"]

        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "invalid_token"},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 400

    async def test_logout_requires_auth(self, client: AsyncClient):
        """Test logout requires authentication."""
        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "some_token"},
        )

        assert response.status_code == 401


@pytest.mark.asyncio
class TestLogoutAllEndpoint:
    """Tests for POST /auth/logout-all endpoint."""

    async def test_logout_all_success(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test logout from all sessions."""
        # Login multiple times
        login_data1 = await login_user(client, test_user.username, test_password)
        login_data2 = await login_user(client, test_user.username, test_password)

        # Logout all with first token
        response = await client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {login_data1['access_token']}"},
        )

        assert response.status_code == 200
        assert "session" in response.json()["message"].lower()


# =============================================================================
# Refresh Token Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
class TestRefreshEndpoint:
    """Tests for POST /auth/refresh endpoint."""

    async def test_refresh_success(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test successful token refresh."""
        login_data = await login_user(client, test_user.username, test_password)

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": login_data["refresh_token"]},
        )

        assert response.status_code == 200, f"Refresh failed: {response.text}"

        data = response.json()
        assert "access_token" in data, "Must return new access token"
        assert "refresh_token" in data, "Must return new refresh token (rotation)"
        assert data["access_token"] != login_data["access_token"], "Access token should be different"

    async def test_refresh_invalid_token(self, client: AsyncClient):
        """Test refresh with invalid token."""
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid_refresh_token"},
        )

        assert response.status_code == 401

    async def test_refresh_used_token_fails(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test that used refresh token cannot be reused (token rotation)."""
        login_data = await login_user(client, test_user.username, test_password)
        original_refresh = login_data["refresh_token"]

        # First refresh - should succeed
        response1 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original_refresh},
        )
        assert response1.status_code == 200

        # Second refresh with same token - should fail (already rotated)
        response2 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original_refresh},
        )
        assert response2.status_code == 401, "Rotated token should be revoked"


# =============================================================================
# Get Current User Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
class TestMeEndpoint:
    """Tests for GET /auth/me endpoint."""

    async def test_get_me_success(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test getting current user info."""
        login_data = await login_user(client, test_user.username, test_password)

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )

        assert response.status_code == 200

        data = response.json()
        assert data["username"] == test_user.username
        assert data["email"] == test_user.email
        assert data["role"] == test_user.role.value
        assert data["is_active"] is True
        assert "id" in data
        assert "created_at" in data

    async def test_get_me_requires_auth(self, client: AsyncClient):
        """Test /me requires authentication."""
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401

    async def test_get_me_invalid_token(self, client: AsyncClient):
        """Test /me with invalid token."""
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid_token"},
        )

        assert response.status_code == 401


# =============================================================================
# Change Password Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
class TestChangePasswordEndpoint:
    """Tests for POST /auth/change-password endpoint."""

    async def test_change_password_success(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test successful password change."""
        login_data = await login_user(client, test_user.username, test_password)

        new_password = "NewSecurePassword456"

        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": test_password,
                "new_password": new_password,
            },
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )

        assert response.status_code == 200, f"Password change failed: {response.text}"
        assert response.json()["success"] is True

        # Verify can login with new password
        response2 = await client.post(
            "/api/v1/auth/login",
            json={"username": test_user.username, "password": new_password},
        )
        assert response2.status_code == 200

    async def test_change_password_wrong_current(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test password change with wrong current password."""
        login_data = await login_user(client, test_user.username, test_password)

        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "WrongCurrentPassword",
                "new_password": "NewPassword123",
            },
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )

        assert response.status_code == 401

    async def test_change_password_weak_new(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test password change with weak new password."""
        login_data = await login_user(client, test_user.username, test_password)

        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": test_password,
                "new_password": "weak",  # Too short, no uppercase, etc.
            },
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )

        assert response.status_code == 400
        assert "VALIDATION_ERROR" in response.json()["detail"]["error_code"]


# =============================================================================
# Sessions Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
class TestSessionsEndpoint:
    """Tests for GET /auth/sessions endpoint."""

    async def test_list_sessions(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test listing active sessions."""
        # Login to create a session
        login_data = await login_user(client, test_user.username, test_password)

        response = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )

        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1, "Should have at least one active session"

        # Check session structure
        session = data[0]
        assert "id" in session
        assert "created_at" in session
        assert "expires_at" in session

    async def test_revoke_session(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test revoking a specific session."""
        # Login twice
        login_data1 = await login_user(client, test_user.username, test_password)
        login_data2 = await login_user(client, test_user.username, test_password)

        # Get sessions
        sessions_response = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {login_data1['access_token']}"},
        )
        sessions = sessions_response.json()

        # Revoke second session
        if len(sessions) > 1:
            session_to_revoke = sessions[1]["id"]

            response = await client.delete(
                f"/api/v1/auth/sessions/{session_to_revoke}",
                headers={"Authorization": f"Bearer {login_data1['access_token']}"},
            )

            assert response.status_code == 200


# =============================================================================
# JWT Token Tests
# =============================================================================


@pytest.mark.asyncio
class TestJWTTokenBehavior:
    """Tests for JWT token behavior."""

    async def test_access_token_expiry_info(
        self, client: AsyncClient, test_user: User, test_password: str
    ):
        """Test that access token expiry is returned correctly."""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": test_user.username, "password": test_password},
        )

        data = response.json()
        expires_in = data["expires_in"]

        # Should be around 30 minutes (1800 seconds)
        assert 1700 <= expires_in <= 1900, f"Expected ~1800 seconds, got {expires_in}"

    async def test_token_contains_user_info(
        self, client: AsyncClient, admin_user: User, test_password: str
    ):
        """Test that JWT contains expected claims via /me endpoint."""
        login_data = await login_user(client, admin_user.username, test_password)

        # Access token should decode to show admin role
        me_response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )

        assert me_response.status_code == 200
        assert me_response.json()["role"] == "admin"


# =============================================================================
# Error Response Tests
# =============================================================================


@pytest.mark.asyncio
class TestAuthErrorResponses:
    """Tests for authentication error responses."""

    async def test_missing_bearer_token(self, client: AsyncClient):
        """Test error when bearer token is missing."""
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401
        assert "error_code" in response.json()["detail"]
        assert "WWW-Authenticate" in response.headers

    async def test_malformed_bearer_token(self, client: AsyncClient):
        """Test error with malformed bearer token."""
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )

        assert response.status_code == 401

    async def test_error_includes_request_id(self, client: AsyncClient):
        """Test that error responses include request ID."""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "password"},
            headers={"X-Request-ID": "test-request-123"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["request_id"] == "test-request-123"
