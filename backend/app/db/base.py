"""
SQLAlchemy Base and Common Mixins

Provides declarative base class and common mixins for all models.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Naming convention for constraints (important for Alembic migrations)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy models.

    Provides:
    - Automatic table naming from class name
    - Consistent metadata with naming convention
    - Common __repr__ implementation
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __tablename__(cls) -> str:
        """Generate table name from class name (snake_case)."""
        # Convert CamelCase to snake_case
        name = cls.__name__
        return "".join(
            ["_" + c.lower() if c.isupper() else c for c in name]
        ).lstrip("_")

    def __repr__(self) -> str:
        """Generate repr with primary key columns."""
        pk_cols = [col.name for col in self.__table__.primary_key.columns]
        pk_values = ", ".join(
            f"{col}={getattr(self, col, None)!r}" for col in pk_cols
        )
        return f"<{self.__class__.__name__}({pk_values})>"

    def to_dict(self) -> dict[str, Any]:
        """Convert model to dictionary."""
        result = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, UUID):
                value = str(value)
            result[column.name] = value
        return result


class TimestampMixin:
    """
    Mixin providing created_at and updated_at columns.

    Automatically sets created_at on insert and updated_at on update.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """
    Mixin providing UUID primary key.
    """

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


# Import all models here to ensure they're registered with Base
# This is important for Alembic migrations
def import_models() -> None:
    """Import all models to register them with SQLAlchemy."""
    from app.models import (  # noqa: F401
        customer,
        device,
        device_activation,
        device_metric,
        device_token,
        payment_trigger,
        transaction,
    )
