"""Initial schema with all models

Revision ID: 001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000

CRITICAL: This migration creates all initial tables.
All migrations run in transactions - any error causes rollback.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Create all initial tables.

    Tables created:
    - customer
    - device
    - transaction
    - device_metric
    - device_activation
    - payment_trigger

    CRITICAL: Ensure all operations are reversible.
    """
    # Create enum types first
    device_type_enum = postgresql.ENUM(
        "solar", "emobility",
        name="device_type_enum",
        create_type=False,
    )
    device_status_enum = postgresql.ENUM(
        "active", "suspended", "inactive",
        name="device_status_enum",
        create_type=False,
    )
    payment_provider_enum = postgresql.ENUM(
        "wave", "qmoney", "apple_pay",
        name="payment_provider_enum",
        create_type=False,
    )
    transaction_status_enum = postgresql.ENUM(
        "pending", "completed", "failed", "refunded",
        name="transaction_status_enum",
        create_type=False,
    )
    metric_type_enum = postgresql.ENUM(
        "energy_consumed", "distance_traveled", "battery_level", "uptime",
        name="metric_type_enum",
        create_type=False,
    )
    activation_type_enum = postgresql.ENUM(
        "payment", "manual", "trial",
        name="activation_type_enum",
        create_type=False,
    )
    trigger_type_enum = postgresql.ENUM(
        "usage_threshold", "time_based", "manual",
        name="trigger_type_enum",
        create_type=False,
    )

    # Create enums
    device_type_enum.create(op.get_bind(), checkfirst=True)
    device_status_enum.create(op.get_bind(), checkfirst=True)
    payment_provider_enum.create(op.get_bind(), checkfirst=True)
    transaction_status_enum.create(op.get_bind(), checkfirst=True)
    metric_type_enum.create(op.get_bind(), checkfirst=True)
    activation_type_enum.create(op.get_bind(), checkfirst=True)
    trigger_type_enum.create(op.get_bind(), checkfirst=True)

    # Create customer table
    op.create_table(
        "customer",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=True, index=True),
        sa.Column("phone", sa.String(20), nullable=True, index=True),
        sa.Column(
            "payment_provider",
            payment_provider_enum,
            nullable=False,
        ),
        sa.Column("payment_provider_id", sa.String(100), nullable=True, index=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
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
    )

    # Customer unique constraint on provider + provider_id
    op.create_unique_constraint(
        "uq_customer_payment_provider_id",
        "customer",
        ["payment_provider", "payment_provider_id"],
    )

    # Create device table
    op.create_table(
        "device",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("device_type", device_type_enum, nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("openpaygo_secret_key", sa.String(500), nullable=False),
        sa.Column(
            "status",
            device_status_enum,
            nullable=False,
            server_default="active",
            index=True,
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
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
    )

    # Device composite indexes
    op.create_index("ix_device_status_type", "device", ["status", "device_type"])
    op.create_index("ix_device_customer_status", "device", ["customer_id", "status"])

    # Create transaction table
    op.create_table(
        "transaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("payment_provider", payment_provider_enum, nullable=False),
        sa.Column(
            "provider_transaction_id",
            sa.String(200),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "status",
            transaction_status_enum,
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(100),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
    )

    # Transaction composite indexes
    op.create_index("ix_transaction_customer_status", "transaction", ["customer_id", "status"])
    op.create_index("ix_transaction_device_status", "transaction", ["device_id", "status"])
    op.create_index(
        "ix_transaction_provider_status", "transaction", ["payment_provider", "status"]
    )

    # Create device_metric table
    op.create_table(
        "device_metric",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("metric_type", metric_type_enum, nullable=False),
        sa.Column("value", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("value >= 0", name="ck_device_metric_value_non_negative"),
    )

    # Device metric composite indexes
    op.create_index(
        "ix_device_metric_device_timestamp", "device_metric", ["device_id", "timestamp"]
    )
    op.create_index(
        "ix_device_metric_device_type_timestamp",
        "device_metric",
        ["device_id", "metric_type", "timestamp"],
    )

    # Create device_activation table
    op.create_table(
        "device_activation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaction.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("activation_token", sa.String(200), nullable=False),
        sa.Column("activation_type", activation_type_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_device_activation_expires_after_created",
        ),
        sa.CheckConstraint(
            "(activation_type != 'payment') OR (transaction_id IS NOT NULL)",
            name="ck_device_activation_payment_requires_transaction",
        ),
    )

    # Device activation composite indexes
    op.create_index(
        "ix_device_activation_device_created",
        "device_activation",
        ["device_id", "created_at"],
    )

    # Create payment_trigger table
    op.create_table(
        "payment_trigger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("trigger_type", trigger_type_enum, nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true", index=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
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
    )

    # Payment trigger unique constraint
    op.create_unique_constraint(
        "uq_payment_trigger_device_type",
        "payment_trigger",
        ["device_id", "trigger_type"],
    )

    # Payment trigger composite indexes
    op.create_index(
        "ix_payment_trigger_device_active",
        "payment_trigger",
        ["device_id", "is_active"],
    )


def downgrade() -> None:
    """
    Drop all tables in reverse order.

    CRITICAL: Must completely reverse upgrade().
    """
    # Drop tables in reverse order of creation (to respect foreign keys)
    op.drop_table("payment_trigger")
    op.drop_table("device_activation")
    op.drop_table("device_metric")
    op.drop_table("transaction")
    op.drop_table("device")
    op.drop_table("customer")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS trigger_type_enum")
    op.execute("DROP TYPE IF EXISTS activation_type_enum")
    op.execute("DROP TYPE IF EXISTS metric_type_enum")
    op.execute("DROP TYPE IF EXISTS transaction_status_enum")
    op.execute("DROP TYPE IF EXISTS payment_provider_enum")
    op.execute("DROP TYPE IF EXISTS device_status_enum")
    op.execute("DROP TYPE IF EXISTS device_type_enum")
