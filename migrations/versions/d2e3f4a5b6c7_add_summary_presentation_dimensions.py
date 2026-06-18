"""add summary presentation dimensions

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE summary_logs ADD COLUMN style_id VARCHAR(64);")
    op.execute("ALTER TABLE summary_logs ADD COLUMN tone_id VARCHAR(64);")
    op.execute("ALTER TABLE summary_logs ADD COLUMN aggressiveness SMALLINT;")
    op.execute("""
        ALTER TABLE summary_logs
        ADD CONSTRAINT ck_summary_logs_aggressiveness
        CHECK (aggressiveness IS NULL OR aggressiveness BETWEEN 0 AND 3);
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE summary_logs DROP CONSTRAINT IF EXISTS ck_summary_logs_aggressiveness;")
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS aggressiveness;")
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS tone_id;")
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS style_id;")
