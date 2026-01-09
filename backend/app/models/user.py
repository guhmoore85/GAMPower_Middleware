"""
User Model for PAYGO Middleware

Represents system users with role-based access control.

CRITICAL: Passwords are hashed with bcrypt before storage.
All authentication events are logged LOUDLY.
"""

import enum
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, Index, String, event
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import audit_logger, get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.refresh_token import RefreshToken

logger = get_logger(__name__)


class UserRole(str, enum.Enum):
    """
    User roles for role-based access control.

    Permissions hierarchy:
    - admin: Full system access, user management
    - technician: Device management, token generation
    - support: Read access, customer support operations
    - readonly: View-only access to dashboards
    """
    ADMIN = "admin"
    TECHNICIAN = "technician"
    SUPPORT = "support"
    READONLY = "readonly"


# Role permissions mapping
ROLE_PERMISSIONS = {
    UserRole.ADMIN: {
        "users:create", "users:read", "users:update", "users:delete",
        "devices:create", "devices:read", "devices:update", "devices:delete",
        "transactions:read", "transactions:refund",
        "tokens:generate", "tokens:revoke",
        "analytics:read", "settings:manage",
    },
    UserRole.TECHNICIAN: {
        "devices:create", "devices:read", "devices:update",
        "transactions:read",
        "tokens:generate", "tokens:revoke",
        "analytics:read",
    },
    UserRole.SUPPORT: {
        "devices:read",
        "transactions:read",
        "analytics:read",
    },
    UserRole.READONLY: {
        "devices:read",
        "transactions:read",
        "analytics:read",
    },
}


def role_has_permission(role: UserRole, permission: str) -> bool:
    """Check if a role has a specific permission."""
    return permission in ROLE_PERMISSIONS.get(role, set())


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    User model for authentication and authorization.

    Attributes:
        id: Unique identifier (UUID)
        username: Unique username for login
        email: Unique email address
        hashed_password: bcrypt-hashed password
        full_name: User's display name
        role: User role for RBAC
        is_active: Whether user can authenticate
        last_login_at: Timestamp of last successful login
    """

    __tablename__ = "user"

    # Username - unique, used for login
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    # Email - unique
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    # Password hash (bcrypt)
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Display name
    full_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Role for RBAC
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"),
        default=UserRole.READONLY,
        nullable=False,
        index=True,
    )

    # Active status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    # Last login timestamp
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        Index("ix_user_role_active", "role", "is_active"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("username")
    def validate_username(self, key: str, value: str) -> str:
        """
        Validate username format.

        Rules:
        - 3-50 characters
        - Alphanumeric, underscores, hyphens
        - Must start with letter

        LOUD: Raises ValueError with details if invalid.
        """
        if not value:
            logger.error("Username is empty")
            raise ValueError("Username cannot be empty")

        if len(value) < 3:
            logger.error("Username too short", length=len(value))
            raise ValueError("Username must be at least 3 characters")

        if len(value) > 50:
            logger.error("Username too long", length=len(value))
            raise ValueError("Username must not exceed 50 characters")

        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", value):
            logger.error("Invalid username format", username=value)
            raise ValueError(
                "Username must start with a letter and contain only "
                "letters, numbers, underscores, and hyphens"
            )

        return value.lower()

    @validates("email")
    def validate_email(self, key: str, value: str) -> str:
        """
        Validate email format.

        LOUD: Raises ValueError if invalid.
        """
        if not value:
            logger.error("Email is empty")
            raise ValueError("Email cannot be empty")

        # Basic email regex (not exhaustive but catches obvious errors)
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", value):
            logger.error("Invalid email format", email=value)
            raise ValueError(f"Invalid email format: {value}")

        if len(value) > 255:
            logger.error("Email too long", length=len(value))
            raise ValueError("Email must not exceed 255 characters")

        return value.lower()

    @validates("role")
    def validate_role(self, key: str, value: Any) -> UserRole:
        """Validate role is a valid enum value."""
        if isinstance(value, str):
            try:
                return UserRole(value)
            except ValueError:
                logger.error(
                    "Invalid user role",
                    value=value,
                    valid_values=[r.value for r in UserRole],
                )
                raise ValueError(
                    f"Invalid user role: {value}. "
                    f"Valid values: {[r.value for r in UserRole]}"
                )
        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return role_has_permission(self.role, permission)

    def can_manage_users(self) -> bool:
        """Check if user can manage other users."""
        return self.role == UserRole.ADMIN

    def can_generate_tokens(self) -> bool:
        """Check if user can generate device tokens."""
        return self.role in {UserRole.ADMIN, UserRole.TECHNICIAN}

    def update_last_login(self) -> None:
        """Update last login timestamp."""
        self.last_login_at = datetime.now(timezone.utc)
        logger.info(
            "User login recorded",
            user_id=str(self.id),
            username=self.username,
        )

    def deactivate(self, reason: str = "") -> None:
        """
        Deactivate user account.

        LOUD: Logs deactivation with reason.
        """
        if not self.is_active:
            logger.warning(
                "Attempted to deactivate already inactive user",
                user_id=str(self.id),
            )
            return

        self.is_active = False

        logger.warning(
            "User deactivated",
            user_id=str(self.id),
            username=self.username,
            reason=reason,
        )

        audit_logger.log_event(
            event_type="user",
            action="deactivate",
            resource_type="user",
            resource_id=str(self.id),
            details={"username": self.username, "reason": reason},
        )

    def activate(self) -> None:
        """
        Activate user account.

        LOUD: Logs activation.
        """
        if self.is_active:
            logger.warning(
                "Attempted to activate already active user",
                user_id=str(self.id),
            )
            return

        self.is_active = True

        logger.info(
            "User activated",
            user_id=str(self.id),
            username=self.username,
        )

        audit_logger.log_event(
            event_type="user",
            action="activate",
            resource_type="user",
            resource_id=str(self.id),
            details={"username": self.username},
        )

    def to_dict_safe(self) -> dict[str, Any]:
        """
        Convert to dictionary without sensitive fields.

        NEVER includes password hash.
        """
        return {
            "id": str(self.id),
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role.value,
            "is_active": self.is_active,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Event listener for logging user creation
@event.listens_for(User, "after_insert")
def user_after_insert(mapper, connection, target):
    """Log user creation."""
    logger.info(
        "User created",
        user_id=str(target.id),
        username=target.username,
        role=target.role.value,
    )

    audit_logger.log_event(
        event_type="user",
        action="create",
        resource_type="user",
        resource_id=str(target.id),
        details={"username": target.username, "role": target.role.value},
    )


# Event listener for logging role changes
@event.listens_for(User.role, "set")
def user_role_set(target, value, oldvalue, initiator):
    """Log user role changes."""
    if oldvalue is not None and oldvalue != value:
        logger.warning(
            "User role changing",
            user_id=str(target.id) if target.id else "new",
            old_role=oldvalue.value if isinstance(oldvalue, UserRole) else oldvalue,
            new_role=value.value if isinstance(value, UserRole) else value,
        )
