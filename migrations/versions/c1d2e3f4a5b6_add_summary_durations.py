"""add summary durations

Revision ID: c1d2e3f4a5b6
Revises: 9b4a1c2d3e4f
Create Date: 2026-06-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "9b4a1c2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE summary_logs
        ADD COLUMN summary_duration_seconds DOUBLE PRECISION;
    """)
    op.execute("""
        ALTER TABLE summary_logs
        ADD COLUMN llm_duration_seconds DOUBLE PRECISION;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS llm_duration_seconds;")
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS summary_duration_seconds;")
