import json
import logging
import re
import time
from typing import Any

from aiogram import types
from redis.asyncio import Redis

import config
from sumbot.constants import HISTORY_SKIP_MARKERS

logger = logging.getLogger("SumBot.history")

HISTORY_TEXT_PATTERN = re.compile(
    r"^\[(?P<created_at>\d{2}\.\d{2}\s\d{2}:\d{2})\]\s*"
    r"(?P<author_name>(?:[^(@:]|\([^)]*\))+?)"
    r"(?:\s*\(@(?P<author_username>[^)]+)\))?"
    r"(?:\s*\(в ответ\s+(?P<reply_to_name>[^)]+)\))?"
    r"\s*:\s*"
    r"(?P<message_text>.*)$"
)


def build_chat_history_key(chat_id: int) -> str:
    return f"chat:{chat_id}:log"


def format_message_for_history(message: types.Message) -> str:
    user_name = message.from_user.first_name if message.from_user else "Unknown"
    if message.from_user and message.from_user.username:
        user_name += f" (@{message.from_user.username})"

    reply_info = ""
    if message.reply_to_message and message.reply_to_message.from_user:
        reply_to = message.reply_to_message.from_user.first_name
        reply_info = f" (в ответ {reply_to})"

    created_at = message.date.strftime("%d.%m %H:%M")
    return f"[{created_at}] {user_name}{reply_info}: {message.text}"


def build_history_message_payload(message: types.Message) -> dict[str, Any] | None:
    if not message.text:
        return None
    return {
        "ts": message.date.timestamp(),
        "text": format_message_for_history(message),
        "message_text": message.text,
        "author_id": getattr(message.from_user, "id", None),
        "author_name": message.from_user.first_name if message.from_user else "Unknown",
        "author_username": getattr(message.from_user, "username", None),
        "reply_to_user_id": (
            message.reply_to_message.from_user.id
            if message.reply_to_message and message.reply_to_message.from_user
            else None
        ),
        "reply_to_username": (
            message.reply_to_message.from_user.username
            if message.reply_to_message and message.reply_to_message.from_user
            else None
        ),
        "reply_to_name": (
            message.reply_to_message.from_user.first_name
            if message.reply_to_message and message.reply_to_message.from_user
            else None
        ),
        "message_id": getattr(message, "message_id", None),
    }


def parse_history_text(text: str) -> dict[str, str | None] | None:
    match = HISTORY_TEXT_PATTERN.match(text)
    if not match:
        return None

    author_name = match.group("author_name").strip()
    if author_name.endswith(")"):
        author_name = author_name.rsplit("(", 1)[0].strip()

    return {
        "author_name": author_name,
        "author_username": match.group("author_username"),
        "reply_to_name": match.group("reply_to_name"),
        "message_text": match.group("message_text").strip(),
    }


async def save_message_to_history(redis_client: Redis, message: types.Message) -> dict[str, Any] | None:
    if not message.text:
        logger.debug("Skipping history write for non-text message (chat_id=%s)", message.chat.id)
        return None
    if message.text.startswith("/"):
        logger.debug(
            "Skipping history write for command message (chat_id=%s, message_id=%s)",
            message.chat.id,
            getattr(message, "message_id", None),
        )
        return None

    payload = build_history_message_payload(message)
    if payload is None:
        return None
    log_entry = json.dumps(payload)
    history_key = build_chat_history_key(message.chat.id)
    try:
        await redis_client.lpush(history_key, log_entry)
        await redis_client.ltrim(history_key, 0, config.ChatConfig.HISTORY_LIMIT - 1)
        logger.debug(
            "Saved message to history (chat_id=%s, message_id=%s, history_limit=%s)",
            message.chat.id,
            getattr(message, "message_id", None),
            config.ChatConfig.HISTORY_LIMIT,
        )
    except Exception:
        logger.exception(
            "Failed to save message to Redis history (chat_id=%s, message_id=%s)",
            message.chat.id,
            getattr(message, "message_id", None),
        )
        raise
    return payload


async def fetch_messages_for_summary(
    redis_client: Redis,
    chat_id: int,
    limit_messages: int | None = None,
    time_limit_seconds: int | None = None,
) -> list[dict[str, Any]]:
    raw_logs = await redis_client.lrange(
        build_chat_history_key(chat_id),
        0,
        config.ChatConfig.HISTORY_LIMIT - 1,
    )
    if not raw_logs:
        logger.info("History is empty for summary request (chat_id=%s)", chat_id)
        return []

    current_ts = time.time()
    selected_messages: list[dict[str, Any]] = []
    skipped_by_marker = 0
    skipped_by_time = 0
    skipped_malformed = 0
    for raw_item in raw_logs:
        try:
            data = json.loads(raw_item)
        except json.JSONDecodeError:
            skipped_malformed += 1
            continue

        message_text = data.get("text")
        message_ts = data.get("ts")
        if not isinstance(message_text, str) or not isinstance(message_ts, int | float):
            skipped_malformed += 1
            continue

        if should_skip_history_item(message_text):
            skipped_by_marker += 1
            continue

        if time_limit_seconds is not None and current_ts - message_ts > time_limit_seconds:
            skipped_by_time += 1
            continue

        selected_messages.append(data)
        if limit_messages is not None and len(selected_messages) >= limit_messages:
            break

    selected_messages.reverse()
    logger.info(
        "Selected history for summary "
        "(chat_id=%s, raw_count=%s, selected_count=%s, limit_messages=%s, "
        "time_limit_seconds=%s, skipped_marker=%s, skipped_time=%s, skipped_malformed=%s)",
        chat_id,
        len(raw_logs),
        len(selected_messages),
        limit_messages,
        time_limit_seconds,
        skipped_by_marker,
        skipped_by_time,
        skipped_malformed,
    )
    return selected_messages


def should_skip_history_item(text: str) -> bool:
    return any(marker in text for marker in HISTORY_SKIP_MARKERS)
