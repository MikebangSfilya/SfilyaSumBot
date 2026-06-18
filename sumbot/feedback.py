import json
import logging
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from sumbot.constants import SUMMARY_FEEDBACK_RATE_LIMIT_SECONDS
from sumbot.database import (
    summary_feedback_exists,
    update_summary_feedback_details,
    upsert_summary_feedback,
    wait_for_summary_log_id,
)

logger = logging.getLogger("SumBot.feedback")

SUMMARY_FEEDBACK_CALLBACK_PREFIX = "summary_feedback:"
SUMMARY_FEEDBACK_DETAILS_CALLBACK_PREFIX = "summary_feedback_details:"
SUMMARY_FEEDBACK_OPTIONS = {
    "good": ("👍 Хорошо", "positive"),
    "neutral": ("😐 Норм", "neutral"),
    "bad": ("👎 Плохо", "negative"),
}
SUMMARY_FEEDBACK_DETAILS_TTL_SECONDS = 15 * 60
SUMMARY_FEEDBACK_DETAILS_MAX_LENGTH = 1000


@dataclass(slots=True)
class PendingFeedbackDetails:
    chat_id: int
    telegram_message_id: int
    user_id: int
    prompt_message_id: int


def build_summary_feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{SUMMARY_FEEDBACK_CALLBACK_PREFIX}{feedback_value}",
                )
                for feedback_value, (label, _sentiment) in SUMMARY_FEEDBACK_OPTIONS.items()
            ],
            [
                InlineKeyboardButton(
                    text="💬 Комментарий",
                    callback_data=SUMMARY_FEEDBACK_DETAILS_CALLBACK_PREFIX,
                )
            ],
        ]
    )


def parse_summary_feedback_callback(data: str | None) -> tuple[str, str] | None:
    if not data or not data.startswith(SUMMARY_FEEDBACK_CALLBACK_PREFIX):
        return None

    feedback_value = data.removeprefix(SUMMARY_FEEDBACK_CALLBACK_PREFIX)
    option = SUMMARY_FEEDBACK_OPTIONS.get(feedback_value)
    if not option:
        return None

    _label, sentiment = option
    return feedback_value, sentiment


def build_pending_feedback_details_key(chat_id: int, user_id: int) -> str:
    return f"pending_feedback_details:{chat_id}:{user_id}"


def build_summary_feedback_rate_limit_key(
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    action: str,
) -> str:
    return f"summary_feedback_rate_limit:{chat_id}:{telegram_message_id}:{user_id}:{action}"


async def acquire_summary_feedback_rate_limit(
    redis_client: Redis,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    action: str,
) -> bool:
    acquired = await redis_client.set(
        build_summary_feedback_rate_limit_key(chat_id, telegram_message_id, user_id, action),
        "1",
        nx=True,
        ex=SUMMARY_FEEDBACK_RATE_LIMIT_SECONDS,
    )
    return bool(acquired)


def normalize_feedback_details(text: str) -> str:
    return text.strip()[:SUMMARY_FEEDBACK_DETAILS_MAX_LENGTH]


async def save_pending_feedback_details(
    redis_client: Redis,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    prompt_message_id: int,
) -> None:
    await redis_client.set(
        build_pending_feedback_details_key(chat_id, user_id),
        json.dumps(
            {
                "chat_id": chat_id,
                "telegram_message_id": telegram_message_id,
                "user_id": user_id,
                "prompt_message_id": prompt_message_id,
            }
        ),
        ex=SUMMARY_FEEDBACK_DETAILS_TTL_SECONDS,
    )


async def get_pending_feedback_details(
    redis_client: Redis,
    chat_id: int,
    user_id: int,
) -> PendingFeedbackDetails | None:
    raw_data = await redis_client.get(build_pending_feedback_details_key(chat_id, user_id))
    if not raw_data:
        return None

    try:
        data = json.loads(raw_data)
        return PendingFeedbackDetails(
            chat_id=int(data["chat_id"]),
            telegram_message_id=int(data["telegram_message_id"]),
            user_id=int(data["user_id"]),
            prompt_message_id=int(data["prompt_message_id"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await clear_pending_feedback_details(redis_client, chat_id, user_id)
        return None


async def clear_pending_feedback_details(redis_client: Redis, chat_id: int, user_id: int) -> None:
    await redis_client.delete(build_pending_feedback_details_key(chat_id, user_id))


async def save_feedback_for_summary(
    db_engine: AsyncEngine | None,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    feedback_value: str,
    sentiment: str,
) -> bool:
    if not db_engine:
        logger.error(
            "DB Error: DATABASE_URL не задан, сохранение feedback невозможно "
            "(chat_id=%s, message_id=%s, user_id=%s, value=%s)",
            chat_id,
            telegram_message_id,
            user_id,
            feedback_value,
        )
        return False

    logger.info(
        "Saving summary feedback "
        "(chat_id=%s, message_id=%s, user_id=%s, value=%s, sentiment=%s)",
        chat_id,
        telegram_message_id,
        user_id,
        feedback_value,
        sentiment,
    )
    try:
        async with db_engine.begin() as conn:
            summary_log_id = await wait_for_summary_log_id(conn, chat_id, telegram_message_id)
            if not summary_log_id:
                logger.warning(
                    "DB: Summary feedback received, but summary is absent in DB "
                    "(chat_id=%s, message_id=%s)",
                    chat_id,
                    telegram_message_id,
                )
                return False

            await upsert_summary_feedback(
                conn,
                summary_log_id,
                chat_id,
                telegram_message_id,
                user_id,
                feedback_value,
                sentiment,
            )
            logger.info(
                "DB: Feedback сохранен "
                "(chat_id=%s, message_id=%s, user_id=%s, value=%s, sentiment=%s)",
                chat_id,
                telegram_message_id,
                user_id,
                feedback_value,
                sentiment,
            )
            return True
    except Exception as exc:
        logger.error("DB Critical Error: Ошибка при сохранении feedback: %s", exc, exc_info=True)
        return False


async def has_feedback_for_summary(
    db_engine: AsyncEngine | None,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
) -> bool:
    if not db_engine:
        logger.error(
            "DB Error: DATABASE_URL не задан, проверка feedback невозможна "
            "(chat_id=%s, message_id=%s, user_id=%s)",
            chat_id,
            telegram_message_id,
            user_id,
        )
        return False

    try:
        async with db_engine.begin() as conn:
            return await summary_feedback_exists(
                conn,
                chat_id,
                telegram_message_id,
                user_id,
            )
    except Exception as exc:
        logger.error("DB Critical Error: Ошибка при проверке feedback: %s", exc, exc_info=True)
        return False


async def save_feedback_details_for_summary(
    db_engine: AsyncEngine | None,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    details: str,
) -> bool:
    if not db_engine:
        logger.error("DB Error: DATABASE_URL не задан, сохранение feedback details невозможно.")
        return False

    try:
        async with db_engine.begin() as conn:
            saved = await update_summary_feedback_details(
                conn,
                chat_id,
                telegram_message_id,
                user_id,
                details,
            )
            if saved:
                logger.info(
                    "DB: Feedback details сохранены "
                    "(chat_id=%s, message_id=%s, user_id=%s)",
                    chat_id,
                    telegram_message_id,
                    user_id,
                )
            else:
                logger.warning(
                    "DB: Feedback details received, but feedback row is absent "
                    "(chat_id=%s, message_id=%s, user_id=%s)",
                    chat_id,
                    telegram_message_id,
                    user_id,
                )
            return saved
    except Exception as exc:
        logger.error(
            "DB Critical Error: Ошибка при сохранении feedback details: %s",
            exc,
            exc_info=True,
        )
        return False
