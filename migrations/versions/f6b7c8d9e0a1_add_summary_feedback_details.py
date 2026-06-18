"""add_summary_feedback_details

Revision ID: f6b7c8d9e0a1
Revises: e2f8a9b1c3d4
Create Date: 2026-05-21 23:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f6b7c8d9e0a1"
down_revision: Union[str, Sequence[str], None] = "e2f8a9b1c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE summary_feedback
        ADD COLUMN details TEXT;
    """)
    op.execute("""
        ALTER TABLE summary_feedback
        ADD COLUMN details_updated_at TIMESTAMP;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE summary_feedback DROP COLUMN IF EXISTS details_updated_at;")
    op.execute("ALTER TABLE summary_feedback DROP COLUMN IF EXISTS details;")
