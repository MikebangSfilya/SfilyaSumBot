"""init_analytics_tables

Revision ID: bbe4fb0a16dc
Revises: 
Create Date: 2026-04-23 03:02:41.395791

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bbe4fb0a16dc'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE prompts (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50),
            system_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    op.execute("""
        CREATE TABLE summary_logs (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            prompt_id INTEGER REFERENCES prompts(id),
            model_name VARCHAR(50),
            input_tokens INTEGER,
            output_tokens INTEGER,
            raw_context TEXT,
            llm_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE summary_logs;")
    op.execute("DROP TABLE prompts;")
