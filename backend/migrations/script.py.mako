"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

CRITICAL: All migrations must be reversible.
Test migrations in a transaction before applying.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """
    Apply migration changes.

    CRITICAL: Ensure all operations are reversible.
    """
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """
    Rollback migration changes.

    CRITICAL: Must completely reverse upgrade().
    """
    ${downgrades if downgrades else "pass"}
