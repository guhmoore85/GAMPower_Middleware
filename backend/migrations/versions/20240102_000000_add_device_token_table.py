"""Add device_token table

Revision ID: 20240102_000000
Revises: 20240101_000000
Create Date: 2024-01-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20240102_000000'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create device_token table for storing OpenPAYGO tokens."""

    # Create device_token table
    op.create_table(
        'device_token',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('transaction_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('token_value', sa.String(length=500), nullable=False),
        sa.Column('token_type', sa.Integer(), nullable=False, default=1),
        sa.Column('days_added', sa.Integer(), nullable=False),
        sa.Column('counter_value', sa.Integer(), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_used', sa.Boolean(), nullable=False, default=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_revoked', sa.Boolean(), nullable=False, default=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_device_token')),
        sa.ForeignKeyConstraint(
            ['device_id'],
            ['device.id'],
            name=op.f('fk_device_token_device_id_device'),
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['transaction_id'],
            ['transaction.id'],
            name=op.f('fk_device_token_transaction_id_transaction'),
            ondelete='SET NULL'
        ),
    )

    # Create indexes for common queries
    op.create_index(
        op.f('ix_device_token_device_id'),
        'device_token',
        ['device_id'],
        unique=False
    )
    op.create_index(
        op.f('ix_device_token_transaction_id'),
        'device_token',
        ['transaction_id'],
        unique=False
    )
    op.create_index(
        op.f('ix_device_token_expires_at'),
        'device_token',
        ['expires_at'],
        unique=False
    )
    op.create_index(
        op.f('ix_device_token_is_used'),
        'device_token',
        ['is_used'],
        unique=False
    )
    op.create_index(
        op.f('ix_device_token_is_revoked'),
        'device_token',
        ['is_revoked'],
        unique=False
    )

    # Composite indexes for efficient queries
    op.create_index(
        'ix_device_token_device_expires',
        'device_token',
        ['device_id', 'expires_at'],
        unique=False
    )
    op.create_index(
        'ix_device_token_device_used',
        'device_token',
        ['device_id', 'is_used'],
        unique=False
    )
    op.create_index(
        'ix_device_token_counter',
        'device_token',
        ['device_id', 'counter_value'],
        unique=False
    )


def downgrade() -> None:
    """Drop device_token table."""

    # Drop indexes
    op.drop_index('ix_device_token_counter', table_name='device_token')
    op.drop_index('ix_device_token_device_used', table_name='device_token')
    op.drop_index('ix_device_token_device_expires', table_name='device_token')
    op.drop_index(op.f('ix_device_token_is_revoked'), table_name='device_token')
    op.drop_index(op.f('ix_device_token_is_used'), table_name='device_token')
    op.drop_index(op.f('ix_device_token_expires_at'), table_name='device_token')
    op.drop_index(op.f('ix_device_token_transaction_id'), table_name='device_token')
    op.drop_index(op.f('ix_device_token_device_id'), table_name='device_token')

    # Drop table
    op.drop_table('device_token')
