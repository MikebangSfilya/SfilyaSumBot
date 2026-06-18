import argparse
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sumbot.constants import SUMMARY_LOG_COUNTER_NAME, SUMMARY_LOG_RETENTION_LIMIT
from sumbot.database import prune_old_summary_logs


async def cleanup_summary_logs(retention_limit: int = SUMMARY_LOG_RETENTION_LIMIT) -> int:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL не найден в .env")
        return 1

    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            before_count = await _count_rows(conn, "summary_logs")
            feedback_before_count = await _count_rows(conn, "summary_feedback")
            pruned_rows = await prune_old_summary_logs(conn, retention_limit)
            after_count = await _count_rows(conn, "summary_logs")
            feedback_after_count = await _count_rows(conn, "summary_feedback")
            total_summary_logs = await _get_counter(conn, SUMMARY_LOG_COUNTER_NAME)

        print(f"summary_logs_before: {before_count}")
        print(f"summary_logs_after: {after_count}")
        print(f"summary_logs_deleted: {pruned_rows}")
        print(f"summary_feedback_before: {feedback_before_count}")
        print(f"summary_feedback_after: {feedback_after_count}")
        print(f"summary_log_retention_limit: {retention_limit}")
        if total_summary_logs is not None:
            print(f"summary_logs_total: {total_summary_logs}")
        return 0
    finally:
        await engine.dispose()


async def _count_rows(conn, table_name: str) -> int:
    result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
    return result.fetchone()[0]


async def _get_counter(conn, counter_name: str) -> int | None:
    result = await conn.execute(
        text("""
            SELECT value
            FROM analytics_counters
            WHERE name = :name
            LIMIT 1
        """),
        {"name": counter_name},
    )
    row = result.fetchone()
    return row[0] if row else None


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Clean old analytics rows from PostgreSQL.")
    parser.add_argument(
        "--limit",
        type=int,
        default=SUMMARY_LOG_RETENTION_LIMIT,
        help="How many latest summary_logs rows to keep.",
    )
    args = parser.parse_args()
    return asyncio.run(cleanup_summary_logs(args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
