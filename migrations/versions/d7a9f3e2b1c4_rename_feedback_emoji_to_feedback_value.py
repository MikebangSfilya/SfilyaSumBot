"""rename_feedback_emoji_to_feedback_value

Revision ID: d7a9f3e2b1c4
Revises: c4d5a66d8f21
Create Date: 2026-05-21 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d7a9f3e2b1c4"
down_revision: Union[str, Sequence[str], None] = "c4d5a66d8f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE summary_feedback RENAME COLUMN emoji TO feedback_value;")


def downgrade() -> None:
    op.execute("ALTER TABLE summary_feedback RENAME COLUMN feedback_value TO emoji;")
