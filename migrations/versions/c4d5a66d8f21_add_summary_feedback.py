"""add_summary_feedback

Revision ID: c4d5a66d8f21
Revises: bbe4fb0a16dc
Create Date: 2026-05-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4d5a66d8f21'
down_revision: Union[str, Sequence[str], None] = 'bbe4fb0a16dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE summary_logs
        ADD COLUMN telegram_message_id BIGINT;
    """)
    op.execute("""
        UPDATE summary_logs
        SET telegram_message_id = 0
        WHERE telegram_message_id IS NULL;
    """)
    op.execute("""
        ALTER TABLE summary_logs
        ALTER COLUMN telegram_message_id SET NOT NULL;
    """)
    op.execute("""
        CREATE INDEX ix_summary_logs_chat_message
        ON summary_logs (chat_id, telegram_message_id);
    """)
    op.execute("""
        CREATE TABLE summary_feedback (
            id SERIAL PRIMARY KEY,
            summary_log_id INTEGER NOT NULL REFERENCES summary_logs(id) ON DELETE CASCADE,
            chat_id BIGINT NOT NULL,
            telegram_message_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            emoji VARCHAR(32) NOT NULL,
            sentiment VARCHAR(16) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_summary_feedback_message_user UNIQUE (chat_id, telegram_message_id, user_id)
        );
    """)
    op.execute("""
        CREATE INDEX ix_summary_feedback_summary_log_id
        ON summary_feedback (summary_log_id);
    """)
    op.execute("""
        CREATE INDEX ix_summary_feedback_chat_message
        ON summary_feedback (chat_id, telegram_message_id);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_summary_feedback_chat_message;")
    op.execute("DROP INDEX IF EXISTS ix_summary_feedback_summary_log_id;")
    op.execute("DROP TABLE IF EXISTS summary_feedback;")
    op.execute("DROP INDEX IF EXISTS ix_summary_logs_chat_message;")
    op.execute("ALTER TABLE summary_logs DROP COLUMN IF EXISTS telegram_message_id;")
