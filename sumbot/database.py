import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from sumbot.constants import (
    SUMMARY_DYNAMIC_EXAMPLE_MAX_CONTEXT_CHARS,
    SUMMARY_DYNAMIC_EXAMPLE_MAX_RESPONSE_CHARS,
    SUMMARY_LOG_COUNTER_NAME,
    SUMMARY_LOG_RETENTION_LIMIT,
)
from sumbot.prompt_builder import SummaryPresentationSettings

logger = logging.getLogger("SumBot.database")


@dataclass(slots=True)
class SummaryExample:
    summary_log_id: int
    input_log: str
    ideal_summary: str


async def save_summary_analytics(
    db_engine: AsyncEngine | None,
    chat_id: int,
    telegram_message_id: int,
    system_prompt: str,
    anonymized_context: str,
    anonymized_response: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    summary_duration_seconds: float,
    llm_duration_seconds: float,
    presentation_settings: SummaryPresentationSettings,
    trigger_source: str = "manual",
) -> None:
    if not db_engine:
        logger.error("DB Error: DATABASE_URL не задан, сохранение невозможно.")
        return

    logger.info(
        "Saving summary analytics "
        "(chat_id=%s, telegram_message_id=%s, model=%s, context_chars=%s, response_chars=%s, "
        "input_tokens=%s, output_tokens=%s, summary_duration=%.2fs, llm_duration=%.2fs, "
        "style=%s, tone=%s, aggressiveness=%s, trigger_source=%s)",
        chat_id,
        telegram_message_id,
        model_name,
        len(anonymized_context),
        len(anonymized_response),
        input_tokens,
        output_tokens,
        summary_duration_seconds,
        llm_duration_seconds,
        presentation_settings.style.option_id,
        presentation_settings.tone.option_id,
        presentation_settings.aggressiveness.level,
        trigger_source,
    )
    try:
        async with db_engine.begin() as conn:
            prompt_id = await _get_or_create_prompt_id(conn, system_prompt)
            logger.debug(
                "Resolved prompt for summary analytics "
                "(chat_id=%s, telegram_message_id=%s, prompt_id=%s)",
                chat_id,
                telegram_message_id,
                prompt_id,
            )
            await _increment_summary_log_counter(conn)
            await _insert_summary_log(
                conn,
                chat_id=chat_id,
                telegram_message_id=telegram_message_id,
                prompt_id=prompt_id,
                model_name=model_name,
                anonymized_context=anonymized_context,
                anonymized_response=anonymized_response,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                summary_duration_seconds=summary_duration_seconds,
                llm_duration_seconds=llm_duration_seconds,
                style_id=presentation_settings.style.option_id,
                tone_id=presentation_settings.tone.option_id,
                aggressiveness=presentation_settings.aggressiveness.level,
                trigger_source=trigger_source,
            )
            pruned_rows = await prune_old_summary_logs(conn, SUMMARY_LOG_RETENTION_LIMIT)
            logger.info(
                "DB: Summary analytics saved "
                "(chat_id=%s, message_id=%s, model=%s, retention_limit=%s, pruned_rows=%s)",
                chat_id,
                telegram_message_id,
                model_name,
                SUMMARY_LOG_RETENTION_LIMIT,
                pruned_rows,
            )
    except Exception as exc:
        logger.error("DB Critical Error: Ошибка при сохранении данных: %s", exc, exc_info=True)


async def fetch_random_good_summary_example(
    db_engine: AsyncEngine | None,
    presentation_settings: SummaryPresentationSettings,
) -> SummaryExample | None:
    if not db_engine:
        logger.info("DB: Dynamic summary example unavailable (db_enabled=False)")
        return None

    try:
        async with db_engine.begin() as conn:
            return await _fetch_random_good_summary_example(conn, presentation_settings)
    except Exception as exc:
        logger.warning(
            "DB: Failed to fetch dynamic summary example (error_type=%s)",
            type(exc).__name__,
            exc_info=True,
        )
        return None


async def _fetch_random_good_summary_example(
    conn: AsyncConnection,
    presentation_settings: SummaryPresentationSettings,
) -> SummaryExample | None:
    result = await conn.execute(
        text("""
            SELECT sl.id,
                   sl.raw_context,
                   sl.llm_response
            FROM summary_logs sl
            WHERE NULLIF(BTRIM(sl.raw_context), '') IS NOT NULL
              AND NULLIF(BTRIM(sl.llm_response), '') IS NOT NULL
              AND LENGTH(sl.raw_context) <= :max_context_chars
              AND LENGTH(sl.llm_response) <= :max_response_chars
              AND sl.style_id = :style_id
              AND sl.tone_id = :tone_id
              AND sl.aggressiveness = :aggressiveness
              AND EXISTS (
                  SELECT 1
                  FROM summary_feedback sf
                  WHERE sf.summary_log_id = sl.id
                    AND sf.feedback_value = :feedback_value
              )
            ORDER BY RANDOM()
            LIMIT 1
        """),
        {
            "feedback_value": "good",
            "max_context_chars": SUMMARY_DYNAMIC_EXAMPLE_MAX_CONTEXT_CHARS,
            "max_response_chars": SUMMARY_DYNAMIC_EXAMPLE_MAX_RESPONSE_CHARS,
            "style_id": presentation_settings.style.option_id,
            "tone_id": presentation_settings.tone.option_id,
            "aggressiveness": presentation_settings.aggressiveness.level,
        },
    )
    row = result.fetchone()
    if not row:
        logger.info("DB: No dynamic summary example found (feedback_value=good)")
        return None

    example = SummaryExample(
        summary_log_id=row[0],
        input_log=row[1].strip(),
        ideal_summary=row[2].strip(),
    )
    logger.info(
        "DB: Dynamic summary example selected "
        "(summary_log_id=%s, input_chars=%s, summary_chars=%s)",
        example.summary_log_id,
        len(example.input_log),
        len(example.ideal_summary),
    )
    return example


async def _increment_summary_log_counter(conn: AsyncConnection) -> None:
    await conn.execute(
        text("""
            INSERT INTO analytics_counters (name, value)
            VALUES (:name, 1)
            ON CONFLICT (name)
            DO UPDATE SET
                value = analytics_counters.value + 1,
                updated_at = CURRENT_TIMESTAMP
        """),
        {"name": SUMMARY_LOG_COUNTER_NAME},
    )


async def _insert_summary_log(
    conn: AsyncConnection,
    chat_id: int,
    telegram_message_id: int,
    prompt_id: int,
    model_name: str,
    anonymized_context: str,
    anonymized_response: str,
    input_tokens: int,
    output_tokens: int,
    summary_duration_seconds: float,
    llm_duration_seconds: float,
    style_id: str,
    tone_id: str,
    aggressiveness: int,
    trigger_source: str,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO summary_logs (
                chat_id,
                prompt_id,
                model_name,
                raw_context,
                llm_response,
                input_tokens,
                output_tokens,
                summary_duration_seconds,
                llm_duration_seconds,
                style_id,
                tone_id,
                aggressiveness,
                trigger_source,
                telegram_message_id
            )
            VALUES (:chat_id, :prompt_id, :model_name, :raw_context, :llm_response,
                    :input_tokens, :output_tokens, :summary_duration_seconds,
                    :llm_duration_seconds, :style_id, :tone_id, :aggressiveness,
                    :trigger_source, :telegram_message_id)
        """),
        {
            "chat_id": chat_id,
            "prompt_id": prompt_id,
            "model_name": model_name,
            "raw_context": anonymized_context,
            "llm_response": anonymized_response,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "summary_duration_seconds": summary_duration_seconds,
            "llm_duration_seconds": llm_duration_seconds,
            "style_id": style_id,
            "tone_id": tone_id,
            "aggressiveness": aggressiveness,
            "trigger_source": trigger_source,
            "telegram_message_id": telegram_message_id,
        },
    )


async def prune_old_summary_logs(conn: AsyncConnection, retention_limit: int) -> int:
    if retention_limit < 1:
        logger.warning("DB: Invalid summary log retention limit %s; skipping prune.", retention_limit)
        return 0

    result = await conn.execute(
        text("""
            WITH old_rows AS (
                SELECT id
                FROM summary_logs
                ORDER BY created_at DESC, id DESC
                OFFSET :retention_limit
            )
            DELETE FROM summary_logs
            WHERE id IN (SELECT id FROM old_rows)
        """),
        {"retention_limit": retention_limit},
    )
    return result.rowcount if result.rowcount is not None else 0


async def _get_or_create_prompt_id(conn: AsyncConnection, system_prompt: str) -> int:
    result = await conn.execute(
        text("SELECT id FROM prompts WHERE system_text = :prompt LIMIT 1"),
        {"prompt": system_prompt},
    )
    row = result.fetchone()
    if row:
        logger.debug("DB: Existing prompt found (prompt_id=%s)", row[0])
        return row[0]

    insert_result = await conn.execute(
        text("INSERT INTO prompts (name, system_text) VALUES ('auto', :prompt) RETURNING id"),
        {"prompt": system_prompt},
    )
    prompt_id = insert_result.fetchone()[0]
    logger.info("DB: New prompt saved (prompt_id=%s, prompt_chars=%s)", prompt_id, len(system_prompt))
    return prompt_id


async def find_summary_log_id(
    conn: AsyncConnection,
    chat_id: int,
    telegram_message_id: int,
) -> int | None:
    result = await conn.execute(
        text("""
            SELECT id
            FROM summary_logs
            WHERE chat_id = :chat_id
              AND telegram_message_id = :message_id
            LIMIT 1
        """),
        {"chat_id": chat_id, "message_id": telegram_message_id},
    )
    row = result.fetchone()
    logger.debug(
        "DB: Summary log lookup finished (chat_id=%s, message_id=%s, found=%s)",
        chat_id,
        telegram_message_id,
        bool(row),
    )
    return row[0] if row else None


async def wait_for_summary_log_id(
    conn: AsyncConnection,
    chat_id: int,
    telegram_message_id: int,
    attempts: int = 3,
    delay_seconds: float = 0.4,
) -> int | None:
    for attempt in range(1, attempts + 1):
        summary_log_id = await find_summary_log_id(conn, chat_id, telegram_message_id)
        if summary_log_id:
            logger.debug(
                "DB: Summary log available for feedback "
                "(chat_id=%s, message_id=%s, summary_log_id=%s, attempt=%s/%s)",
                chat_id,
                telegram_message_id,
                summary_log_id,
                attempt,
                attempts,
            )
            return summary_log_id
        await asyncio.sleep(delay_seconds)
    logger.warning(
        "DB: Summary log was not found after waiting "
        "(chat_id=%s, message_id=%s, attempts=%s)",
        chat_id,
        telegram_message_id,
        attempts,
    )
    return None


async def upsert_summary_feedback(
    conn: AsyncConnection,
    summary_log_id: int,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    feedback_value: str,
    sentiment: str,
) -> None:
    logger.debug(
        "DB: Upserting summary feedback "
        "(summary_log_id=%s, chat_id=%s, message_id=%s, user_id=%s, value=%s, sentiment=%s)",
        summary_log_id,
        chat_id,
        telegram_message_id,
        user_id,
        feedback_value,
        sentiment,
    )
    await conn.execute(
        text("""
            INSERT INTO summary_feedback (
                summary_log_id, chat_id, telegram_message_id, user_id, feedback_value, sentiment
            )
            VALUES (:summary_log_id, :chat_id, :message_id, :user_id, :feedback_value, :sentiment)
            ON CONFLICT (chat_id, telegram_message_id, user_id)
            DO UPDATE SET
                summary_log_id = EXCLUDED.summary_log_id,
                feedback_value = EXCLUDED.feedback_value,
                sentiment = EXCLUDED.sentiment,
                updated_at = CURRENT_TIMESTAMP
        """),
        {
            "summary_log_id": summary_log_id,
            "chat_id": chat_id,
            "message_id": telegram_message_id,
            "user_id": user_id,
            "feedback_value": feedback_value,
            "sentiment": sentiment,
        },
    )


async def summary_feedback_exists(
    conn: AsyncConnection,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
) -> bool:
    result = await conn.execute(
        text("""
            SELECT 1
            FROM summary_feedback
            WHERE chat_id = :chat_id
              AND telegram_message_id = :message_id
              AND user_id = :user_id
            LIMIT 1
        """),
        {
            "chat_id": chat_id,
            "message_id": telegram_message_id,
            "user_id": user_id,
        },
    )
    return result.fetchone() is not None


async def update_summary_feedback_details(
    conn: AsyncConnection,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    details: str,
) -> bool:
    result = await conn.execute(
        text("""
            UPDATE summary_feedback
            SET details = :details,
                details_updated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = :chat_id
              AND telegram_message_id = :message_id
              AND user_id = :user_id
            RETURNING id
        """),
        {
            "chat_id": chat_id,
            "message_id": telegram_message_id,
            "user_id": user_id,
            "details": details,
        },
    )
    return result.fetchone() is not None
