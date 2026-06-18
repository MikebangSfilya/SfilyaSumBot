"""add_summary_log_counter_and_retention

Revision ID: f8b7c2d9e0a1
Revises: e2f8a9b1c3d4
Create Date: 2026-05-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f8b7c2d9e0a1"
down_revision: Union[str, Sequence[str], None] = "e2f8a9b1c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE analytics_counters (
            name VARCHAR(64) PRIMARY KEY,
            value BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    op.execute("""
        INSERT INTO analytics_counters (name, value)
        SELECT 'summary_logs_written', COUNT(*)
        FROM summary_logs
        ON CONFLICT (name) DO NOTHING;
    """)
    op.execute("""
        WITH old_rows AS (
            SELECT id
            FROM summary_logs
            ORDER BY created_at DESC, id DESC
            OFFSET 200
        )
        DELETE FROM summary_logs
        WHERE id IN (SELECT id FROM old_rows);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_counters;")
