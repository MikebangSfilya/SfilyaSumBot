import logging
import time
from typing import Any

import config
from sumbot.chat_registry import normalize_chat_type
from sumbot.history import build_chat_history_key

logger = logging.getLogger("SumBot.engagement")

ONBOARDING_PENDING_KEY_PREFIX = "engagement:onboarding_pending"
ONBOARDING_READY_SENT_KEY_PREFIX = "engagement:onboarding_ready_sent"
CHAT_ACTIVATED_KEY_PREFIX = "engagement:chat_activated"
LAST_MANUAL_SUMMARY_KEY_PREFIX = "engagement:last_manual_summary"


def build_onboarding_pending_key(chat_id: int) -> str:
    return f"{ONBOARDING_PENDING_KEY_PREFIX}:{chat_id}"


def build_onboarding_ready_sent_key(chat_id: int) -> str:
    return f"{ONBOARDING_READY_SENT_KEY_PREFIX}:{chat_id}"


def build_chat_activated_key(chat_id: int) -> str:
    return f"{CHAT_ACTIVATED_KEY_PREFIX}:{chat_id}"


def build_last_manual_summary_key(chat_id: int) -> str:
    return f"{LAST_MANUAL_SUMMARY_KEY_PREFIX}:{chat_id}"


async def start_chat_onboarding(redis: Any, chat_id: int) -> None:
    await redis.set(
        build_onboarding_pending_key(chat_id),
        "1",
        ex=config.ONBOARDING_PENDING_TTL_SECONDS,
    )
    await redis.delete(build_onboarding_ready_sent_key(chat_id))


async def mark_chat_activated(redis: Any, chat_id: int) -> None:
    await redis.set(build_chat_activated_key(chat_id), "1")
    await redis.delete(build_onboarding_pending_key(chat_id))


async def record_manual_summary_request(redis: Any, chat_id: int, *, requested_at: float | None = None) -> None:
    timestamp = requested_at if requested_at is not None else time.time()
    await redis.set(
        build_last_manual_summary_key(chat_id),
        str(timestamp),
        ex=max(config.DAILY_DIGEST_MANUAL_SUPPRESSION_SECONDS * 2, 24 * 3600),
    )


async def was_manual_summary_requested_recently(
    redis: Any,
    chat_id: int,
    *,
    current_time: float | None = None,
    window_seconds: int | None = None,
) -> bool:
    suppression_window = (
        config.DAILY_DIGEST_MANUAL_SUPPRESSION_SECONDS
        if window_seconds is None
        else window_seconds
    )
    if suppression_window <= 0:
        return False

    raw_timestamp = await redis.get(build_last_manual_summary_key(chat_id))
    if isinstance(raw_timestamp, bytes):
        raw_timestamp = raw_timestamp.decode()
    if not raw_timestamp:
        return False

    try:
        requested_at = float(raw_timestamp)
    except (TypeError, ValueError):
        logger.warning("Invalid manual summary timestamp (chat_id=%s, value=%r)", chat_id, raw_timestamp)
        return False

    now = current_time if current_time is not None else time.time()
    return 0 <= now - requested_at <= suppression_window


async def maybe_send_onboarding_ready_hint(redis: Any, message: Any) -> bool:
    chat_id = message.chat.id
    chat_type = normalize_chat_type(getattr(message.chat, "type", None))
    if chat_type not in {"group", "supergroup"}:
        return False
    if not await redis.get(build_onboarding_pending_key(chat_id)):
        return False
    if await redis.get(build_chat_activated_key(chat_id)):
        return False

    message_count = await redis.llen(build_chat_history_key(chat_id))
    if message_count < config.ONBOARDING_READY_MESSAGE_COUNT:
        return False

    ready_key = build_onboarding_ready_sent_key(chat_id)
    acquired = await redis.set(
        ready_key,
        "1",
        nx=True,
        ex=config.ONBOARDING_PENDING_TTL_SECONDS,
    )
    if not acquired:
        return False

    try:
        await message.answer(
            "Истории уже достаточно. Можно попробовать /summary — я перескажу свежие сообщения чата."
        )
    except Exception:
        await redis.delete(ready_key)
        raise
    return True
