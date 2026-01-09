"""Add user and refresh_token tables for JWT authentication

Revision ID: 20240103_000000
Revises: 20240102_000000
Create Date: 2024-01-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20240103_000000"
down_revision: Union[str, None] = "20240102_000000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create user_role_enum type
    user_role_enum = postgresql.ENUM(
        "admin",
        "technician",
        "support",
        "readonly",
        name="user_role_enum",
    )
    user_role_enum.create(op.get_bind(), checkfirst=True)

    # Create user table
    op.create_table(
        "user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(100), nullable=True),
        sa.Column(
            "role",
            user_role_enum,
            nullable=False,
            server_default="readonly",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
        sa.UniqueConstraint("email", name=op.f("uq_user_email")),
    )

    # Create indexes for user table
    op.create_index(op.f("ix_user_username"), "user", ["username"], unique=True)
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)
    op.create_index(op.f("ix_user_role"), "user", ["role"], unique=False)
    op.create_index(op.f("ix_user_is_active"), "user", ["is_active"], unique=False)
    op.create_index("ix_user_role_active", "user", ["role", "is_active"], unique=False)

    # Create refresh_token table
    op.create_table(
        "refresh_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_info", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_token")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_refresh_token_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_token_token_hash")),
    )

    # Create indexes for refresh_token table
    op.create_index(
        op.f("ix_refresh_token_user_id"), "refresh_token", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_refresh_token_token_hash"),
        "refresh_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_refresh_token_expires_at"),
        "refresh_token",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_token_is_revoked"),
        "refresh_token",
        ["is_revoked"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_token_user_active",
        "refresh_token",
        ["user_id", "is_revoked"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_token_expires_revoked",
        "refresh_token",
        ["expires_at", "is_revoked"],
        unique=False,
    )


def downgrade() -> None:
    # Drop refresh_token table and indexes
    op.drop_index("ix_refresh_token_expires_revoked", table_name="refresh_token")
    op.drop_index("ix_refresh_token_user_active", table_name="refresh_token")
    op.drop_index(op.f("ix_refresh_token_is_revoked"), table_name="refresh_token")
    op.drop_index(op.f("ix_refresh_token_expires_at"), table_name="refresh_token")
    op.drop_index(op.f("ix_refresh_token_token_hash"), table_name="refresh_token")
    op.drop_index(op.f("ix_refresh_token_user_id"), table_name="refresh_token")
    op.drop_table("refresh_token")

    # Drop user table and indexes
    op.drop_index("ix_user_role_active", table_name="user")
    op.drop_index(op.f("ix_user_is_active"), table_name="user")
    op.drop_index(op.f("ix_user_role"), table_name="user")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_index(op.f("ix_user_username"), table_name="user")
    op.drop_table("user")

    # Drop enum type
    user_role_enum = postgresql.ENUM(
        "admin",
        "technician",
        "support",
        "readonly",
        name="user_role_enum",
    )
    user_role_enum.drop(op.get_bind(), checkfirst=True)
