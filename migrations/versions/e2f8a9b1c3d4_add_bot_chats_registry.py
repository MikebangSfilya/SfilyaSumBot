"""add_bot_chats_registry

Revision ID: e2f8a9b1c3d4
Revises: d7a9f3e2b1c4
Create Date: 2026-05-21 22:50:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e2f8a9b1c3d4"
down_revision: Union[str, Sequence[str], None] = "d7a9f3e2b1c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE bot_chats (
            chat_id BIGINT PRIMARY KEY,
            chat_type VARCHAR(32) NOT NULL,
            title TEXT NOT NULL,
            username VARCHAR(255),
            is_public BOOLEAN NOT NULL DEFAULT TRUE,
            public_link TEXT,
            bot_status VARCHAR(32) NOT NULL DEFAULT 'seen',
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_bot_chats_public_only CHECK (is_public = TRUE)
        );
    """)
    op.execute("""
        CREATE INDEX ix_bot_chats_status_last_seen
        ON bot_chats (bot_status, last_seen_at DESC);
    """)
    op.execute("""
        CREATE INDEX ix_bot_chats_is_public
        ON bot_chats (is_public);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_bot_chats_is_public;")
    op.execute("DROP INDEX IF EXISTS ix_bot_chats_status_last_seen;")
    op.execute("DROP TABLE IF EXISTS bot_chats;")
