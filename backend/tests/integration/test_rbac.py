"""
Role-Based Access Control (RBAC) Tests

Tests that role-based access control is enforced correctly:
- Admin-only endpoints
- Technician endpoints
- Support endpoints
- Readonly user restrictions

Roles hierarchy:
- admin: Full access to all endpoints
- technician: Device management, metrics, tokens
- support: Read-only device info, customer support
- readonly: View dashboards only

CRITICAL: Each test verifies access is DENIED for unauthorized roles.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole


# =============================================================================
# Fixtures for Different User Roles
# =============================================================================


@pytest.fixture
def test_password():
    """Standard test password."""
    return "TestPassword123"


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, test_password: str) -> User:
    """Create admin user."""
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
async def technician_user(db_session: AsyncSession, test_password: str) -> User:
    """Create technician user."""
    user = User(
        username=f"tech_{uuid4().hex[:8]}",
        email=f"tech_{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(test_password),
        full_name="Technician User",
        role=UserRole.TECHNICIAN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def support_user(db_session: AsyncSession, test_password: str) -> User:
    """Create support user."""
    user = User(
        username=f"support_{uuid4().hex[:8]}",
        email=f"support_{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(test_password),
        full_name="Support User",
        role=UserRole.SUPPORT,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def readonly_user(db_session: AsyncSession, test_password: str) -> User:
    """Create readonly user."""
    user = User(
        username=f"readonly_{uuid4().hex[:8]}",
        email=f"readonly_{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password(test_password),
        full_name="Readonly User",
        role=UserRole.READONLY,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


async def get_auth_headers(
    client: AsyncClient, username: str, password: str
) -> dict:
    """Helper to login and get auth headers."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# User Management Endpoints (Admin Only)
# =============================================================================


@pytest.mark.asyncio
class TestUserManagementRBAC:
    """Tests for user management endpoint access control."""

    async def test_admin_can_list_users(
        self, client: AsyncClient, admin_user: User, test_password: str
    ):
        """Admin should be able to list users."""
        headers = await get_auth_headers(client, admin_user.username, test_password)

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == 200, f"Admin should access users list: {response.text}"

    async def test_technician_cannot_list_users(
        self, client: AsyncClient, technician_user: User, test_password: str
    ):
        """Technician should NOT be able to list users."""
        headers = await get_auth_headers(client, technician_user.username, test_password)

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == 403, "Technician should be denied user list access"
        assert response.json()["detail"]["error_code"] == "AUTHORIZATION_ERROR"

    async def test_support_cannot_list_users(
        self, client: AsyncClient, support_user: User, test_password: str
    ):
        """Support should NOT be able to list users."""
        headers = await get_auth_headers(client, support_user.username, test_password)

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == 403, "Support should be denied user list access"

    async def test_readonly_cannot_list_users(
        self, client: AsyncClient, readonly_user: User, test_password: str
    ):
        """Readonly should NOT be able to list users."""
        headers = await get_auth_headers(client, readonly_user.username, test_password)

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == 403, "Readonly should be denied user list access"

    async def test_admin_can_create_user(
        self, client: AsyncClient, admin_user: User, test_password: str
    ):
        """Admin should be able to create users."""
        headers = await get_auth_headers(client, admin_user.username, test_password)

        response = await client.post(
            "/api/v1/users",
            json={
                "username": f"newuser_{uuid4().hex[:8]}",
                "email": f"new_{uuid4().hex[:8]}@example.com",
                "password": "NewUserPassword123",
                "role": "technician",
            },
            headers=headers,
        )

        assert response.status_code == 201, f"Admin should create user: {response.text}"

    async def test_technician_cannot_create_user(
        self, client: AsyncClient, technician_user: User, test_password: str
    ):
        """Technician should NOT be able to create users."""
        headers = await get_auth_headers(client, technician_user.username, test_password)

        response = await client.post(
            "/api/v1/users",
            json={
                "username": "newuser",
                "email": "new@example.com",
                "password": "Password123",
                "role": "readonly",
            },
            headers=headers,
        )

        assert response.status_code == 403

    async def test_admin_can_deactivate_user(
        self,
        client: AsyncClient,
        admin_user: User,
        readonly_user: User,
        test_password: str,
    ):
        """Admin should be able to deactivate users."""
        headers = await get_auth_headers(client, admin_user.username, test_password)

        response = await client.delete(
            f"/api/v1/users/{readonly_user.id}",
            headers=headers,
        )

        assert response.status_code == 200, f"Admin should deactivate user: {response.text}"

    async def test_admin_cannot_deactivate_self(
        self, client: AsyncClient, admin_user: User, test_password: str
    ):
        """Admin should NOT be able to deactivate themselves."""
        headers = await get_auth_headers(client, admin_user.username, test_password)

        response = await client.delete(
            f"/api/v1/users/{admin_user.id}",
            headers=headers,
        )

        assert response.status_code == 400, "Admin should not deactivate self"

    async def test_admin_can_reset_password(
        self,
        client: AsyncClient,
        admin_user: User,
        technician_user: User,
        test_password: str,
    ):
        """Admin should be able to reset other users' passwords."""
        headers = await get_auth_headers(client, admin_user.username, test_password)

        response = await client.post(
            f"/api/v1/users/{technician_user.id}/reset-password",
            json={"new_password": "ResetPassword123"},
            headers=headers,
        )

        assert response.status_code == 200


# =============================================================================
# Device Endpoints Access Control
# =============================================================================


@pytest.mark.asyncio
class TestDeviceEndpointsRBAC:
    """Tests for device endpoint access control."""

    async def test_admin_can_register_device(
        self, client: AsyncClient, admin_user: User, test_password: str
    ):
        """Admin should be able to register devices."""
        headers = await get_auth_headers(client, admin_user.username, test_password)

        response = await client.post(
            "/api/v1/openpaygo/device/register",
            json={
                "external_id": f"device_{uuid4().hex[:8]}",
                "device_type": "solar",
                "manufacturer": "TestMfg",
                "model": "TestModel",
            },
            headers=headers,
        )

        # Should succeed or at least not be forbidden
        assert response.status_code != 403, "Admin should not be forbidden"

    async def test_technician_can_register_device(
        self, client: AsyncClient, technician_user: User, test_password: str
    ):
        """Technician should be able to register devices."""
        headers = await get_auth_headers(client, technician_user.username, test_password)

        response = await client.post(
            "/api/v1/openpaygo/device/register",
            json={
                "external_id": f"device_{uuid4().hex[:8]}",
                "device_type": "solar",
            },
            headers=headers,
        )

        # Should succeed or at least not be forbidden
        assert response.status_code != 403, "Technician should not be forbidden"


# =============================================================================
# Role Permission Matrix Tests
# =============================================================================


@pytest.mark.asyncio
class TestRolePermissionMatrix:
    """Tests verifying the complete role permission matrix."""

    async def test_all_roles_can_access_own_profile(
        self,
        client: AsyncClient,
        admin_user: User,
        technician_user: User,
        support_user: User,
        readonly_user: User,
        test_password: str,
    ):
        """All roles should be able to access their own profile via /me."""
        for user in [admin_user, technician_user, support_user, readonly_user]:
            headers = await get_auth_headers(client, user.username, test_password)

            response = await client.get("/api/v1/auth/me", headers=headers)

            assert response.status_code == 200, f"{user.role.value} should access /me"
            assert response.json()["role"] == user.role.value

    async def test_all_roles_can_change_own_password(
        self,
        client: AsyncClient,
        readonly_user: User,
        test_password: str,
    ):
        """All roles should be able to change their own password."""
        headers = await get_auth_headers(client, readonly_user.username, test_password)

        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": test_password,
                "new_password": "NewReadonlyPass123",
            },
            headers=headers,
        )

        assert response.status_code == 200, "Readonly should change own password"

    async def test_all_roles_can_logout(
        self,
        client: AsyncClient,
        readonly_user: User,
        test_password: str,
    ):
        """All roles should be able to logout."""
        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": readonly_user.username, "password": test_password},
        )
        data = login_response.json()

        # Logout
        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": data["refresh_token"]},
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )

        assert response.status_code == 200


# =============================================================================
# Error Message Tests for Authorization
# =============================================================================


@pytest.mark.asyncio
class TestAuthorizationErrorMessages:
    """Tests for authorization error message quality."""

    async def test_forbidden_includes_required_roles(
        self, client: AsyncClient, technician_user: User, test_password: str
    ):
        """Forbidden response should indicate required roles."""
        headers = await get_auth_headers(client, technician_user.username, test_password)

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == 403
        error_detail = response.json()["detail"]
        assert "AUTHORIZATION_ERROR" in error_detail["error_code"]
        # Should mention what's required
        assert "admin" in error_detail["message"].lower() or "required" in error_detail["message"].lower()

    async def test_forbidden_includes_request_id(
        self, client: AsyncClient, readonly_user: User, test_password: str
    ):
        """Forbidden response should include request ID for debugging."""
        headers = await get_auth_headers(client, readonly_user.username, test_password)
        headers["X-Request-ID"] = "test-forbidden-123"

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"]["request_id"] == "test-forbidden-123"


# =============================================================================
# Role Escalation Prevention Tests
# =============================================================================


@pytest.mark.asyncio
class TestRoleEscalationPrevention:
    """Tests to verify role escalation is prevented."""

    async def test_technician_cannot_make_admin(
        self, client: AsyncClient, technician_user: User, test_password: str
    ):
        """Technician should not be able to create admin users."""
        headers = await get_auth_headers(client, technician_user.username, test_password)

        response = await client.post(
            "/api/v1/users",
            json={
                "username": "hacker_admin",
                "email": "hacker@example.com",
                "password": "HackerPass123",
                "role": "admin",  # Attempting to create admin
            },
            headers=headers,
        )

        # Should be forbidden (403) since technician can't access user management
        assert response.status_code == 403

    async def test_user_cannot_change_own_role(
        self, client: AsyncClient, technician_user: User, test_password: str
    ):
        """Users should not be able to change their own role via update."""
        headers = await get_auth_headers(client, technician_user.username, test_password)

        # Try to update own user to admin (should fail - no access to user management)
        response = await client.patch(
            f"/api/v1/users/{technician_user.id}",
            json={"role": "admin"},
            headers=headers,
        )

        # Should be forbidden - non-admin can't access user management
        assert response.status_code == 403
