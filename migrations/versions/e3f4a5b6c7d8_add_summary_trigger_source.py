"""add summary trigger source

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-17 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE summary_logs ADD COLUMN trigger_source VARCHAR(32) NOT NULL DEFAULT 'manual';")


def downgrade() -> None:
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS trigger_source;")
