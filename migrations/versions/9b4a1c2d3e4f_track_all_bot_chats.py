"""track_all_bot_chats

Revision ID: 9b4a1c2d3e4f
Revises: a1b2c3d4e5f6
Create Date: 2026-05-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9b4a1c2d3e4f"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE bot_chats DROP CONSTRAINT IF EXISTS ck_bot_chats_public_only;")


def downgrade() -> None:
    op.execute("DELETE FROM bot_chats WHERE is_public = FALSE;")
    op.execute("""
        ALTER TABLE bot_chats
        ADD CONSTRAINT ck_bot_chats_public_only CHECK (is_public = TRUE);
    """)
