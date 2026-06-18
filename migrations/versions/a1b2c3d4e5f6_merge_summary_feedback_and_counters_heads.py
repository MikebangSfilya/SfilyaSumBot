"""merge_summary_feedback_and_counters_heads

Revision ID: a1b2c3d4e5f6
Revises: f6b7c8d9e0a1, f8b7c2d9e0a1
Create Date: 2026-05-22 00:30:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = ("f6b7c8d9e0a1", "f8b7c2d9e0a1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
