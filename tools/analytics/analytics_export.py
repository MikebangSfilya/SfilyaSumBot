import json
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sumbot.constants import SUMMARY_LOG_COUNTER_NAME

AnalyticsDataset = Literal["all", "summary", "feedback", "chats"]
ANALYTICS_DATASETS = ("all", "summary", "feedback", "chats")


SUMMARY_QUERY_SQL = """
    SELECT sl.id,
           sl.chat_id,
           sl.telegram_message_id,
           p.system_text   as prompt,
           sl.raw_context  as context,
           sl.llm_response as summary,
           sl.model_name   as model,
           sl.style_id,
           sl.tone_id,
           sl.aggressiveness,
           sl.summary_duration_seconds,
           sl.llm_duration_seconds,
           sl.created_at
    FROM summary_logs sl
             JOIN prompts p ON sl.prompt_id = p.id
    ORDER BY sl.created_at DESC, sl.id DESC
"""

FEEDBACK_QUERY_SQL = """
    SELECT sf.id,
           sf.summary_log_id,
           sf.chat_id,
           sf.telegram_message_id,
           sf.user_id,
           sf.feedback_value,
           sf.sentiment,
           sf.details,
           p.system_text   as prompt,
           sl.llm_response as summary,
           sl.model_name   as model,
           sl.style_id,
           sl.tone_id,
           sl.aggressiveness,
           sl.summary_duration_seconds,
           sl.llm_duration_seconds,
           sf.created_at,
           sf.updated_at,
           sf.details_updated_at
    FROM summary_feedback sf
             JOIN summary_logs sl ON sf.summary_log_id = sl.id
             JOIN prompts p ON sl.prompt_id = p.id
    ORDER BY sf.created_at DESC
"""

LIMITED_FEEDBACK_QUERY_SQL = """
    WITH selected_summary_logs AS (
        SELECT id
        FROM summary_logs
        ORDER BY created_at DESC, id DESC
        LIMIT :summary_log_limit
    )
    SELECT sf.id,
           sf.summary_log_id,
           sf.chat_id,
           sf.telegram_message_id,
           sf.user_id,
           sf.feedback_value,
           sf.sentiment,
           sf.details,
           p.system_text   as prompt,
           sl.llm_response as summary,
           sl.model_name   as model,
           sl.style_id,
           sl.tone_id,
           sl.aggressiveness,
           sl.summary_duration_seconds,
           sl.llm_duration_seconds,
           sf.created_at,
           sf.updated_at,
           sf.details_updated_at
    FROM summary_feedback sf
             JOIN summary_logs sl ON sf.summary_log_id = sl.id
             JOIN selected_summary_logs selected_logs ON selected_logs.id = sl.id
             JOIN prompts p ON sl.prompt_id = p.id
    ORDER BY sf.created_at DESC
"""

BOT_CHATS_QUERY_SQL = """
    SELECT chat_id,
           chat_type,
           title,
           username,
           is_public,
           public_link,
           bot_status,
           first_seen_at,
           last_seen_at,
           updated_at
    FROM bot_chats
    ORDER BY last_seen_at DESC, chat_id ASC
"""

SUMMARY_LOG_TOTAL_QUERY = text("""
    SELECT value
    FROM analytics_counters
    WHERE name = :name
    LIMIT 1
""")


def _serialize_row(row, datetime_fields: tuple[str, ...]) -> dict:
    item = dict(row._mapping)
    for field in datetime_fields:
        value = item.get(field)
        if value is not None:
            item[field] = value.isoformat()
    return item


def _validate_summary_log_limit(summary_log_limit: int | None) -> None:
    if summary_log_limit is not None and summary_log_limit < 1:
        raise ValueError("summary_log_limit must be a positive integer")


async def fetch_analytics_datasets(
    db_url: str,
    summary_log_limit: int | None = None,
) -> tuple[list[dict], list[dict], list[dict], int | None]:
    _validate_summary_log_limit(summary_log_limit)

    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            query_params = {}
            summary_query_sql = SUMMARY_QUERY_SQL
            feedback_query_sql = FEEDBACK_QUERY_SQL
            if summary_log_limit is not None:
                query_params["summary_log_limit"] = summary_log_limit
                summary_query_sql = f"{SUMMARY_QUERY_SQL}\nLIMIT :summary_log_limit"
                feedback_query_sql = LIMITED_FEEDBACK_QUERY_SQL

            summary_result = await conn.execute(text(summary_query_sql), query_params)
            feedback_result = await conn.execute(text(feedback_query_sql), query_params)
            bot_chats_result = await conn.execute(text(BOT_CHATS_QUERY_SQL))
            total_result = await conn.execute(
                SUMMARY_LOG_TOTAL_QUERY,
                {"name": SUMMARY_LOG_COUNTER_NAME},
            )

            summary_rows = [
                _serialize_row(row, ("created_at",))
                for row in summary_result.fetchall()
            ]
            feedback_rows = [
                _serialize_row(row, ("created_at", "updated_at", "details_updated_at"))
                for row in feedback_result.fetchall()
            ]
            bot_chat_rows = [
                _serialize_row(row, ("first_seen_at", "last_seen_at", "updated_at"))
                for row in bot_chats_result.fetchall()
            ]
            total_row = total_result.fetchone()
            total_summary_logs = total_row[0] if total_row else None
        return summary_rows, feedback_rows, bot_chat_rows, total_summary_logs
    finally:
        await engine.dispose()


def build_export_payloads(
    summary_rows: list[dict],
    feedback_rows: list[dict],
    bot_chat_rows: list[dict] | None = None,
    dataset: AnalyticsDataset = "all",
) -> dict[str, bytes]:
    if dataset not in ANALYTICS_DATASETS:
        raise ValueError(f"Unknown analytics dataset: {dataset}")

    payloads = {}
    if dataset in {"all", "summary"}:
        payloads["summary_dataset.json"] = json.dumps(
            summary_rows,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    if dataset in {"all", "feedback"}:
        payloads["summary_feedback_dataset.json"] = json.dumps(
            feedback_rows,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    if dataset in {"all", "chats"}:
        payloads["bot_chats_dataset.json"] = json.dumps(
            bot_chat_rows or [],
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    return payloads


def build_export_caption(
    summary_rows: list[dict],
    feedback_rows: list[dict],
    bot_chat_rows: list[dict] | None = None,
    dataset: AnalyticsDataset = "all",
    total_summary_logs: int | None = None,
    summary_log_limit: int | None = None,
) -> str:
    if dataset not in ANALYTICS_DATASETS:
        raise ValueError(f"Unknown analytics dataset: {dataset}")
    _validate_summary_log_limit(summary_log_limit)

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "Analytics export",
        f"dataset: {dataset}",
    ]
    if summary_log_limit is not None:
        lines.append(f"summary_logs_limit: {summary_log_limit}")
    if dataset in {"all", "summary"}:
        lines.append(f"summary_logs: {len(summary_rows)}")
        if total_summary_logs is not None:
            lines.append(f"summary_logs_total: {total_summary_logs}")
    if dataset in {"all", "feedback"}:
        lines.append(f"summary_feedback: {len(feedback_rows)}")
    if dataset in {"all", "chats"}:
        lines.append(f"bot_chats: {len(bot_chat_rows or [])}")
    lines.append(f"generated_at: {exported_at}")
    return "\n".join(lines)
