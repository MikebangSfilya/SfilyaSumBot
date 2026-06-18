from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import config
from sumbot.history import parse_history_text

if TYPE_CHECKING:
    from anonymizer import Anonymizer

logger = logging.getLogger("SumBot.summary_context")

SAFE_MERGE_MAX_CHARS = 80
SAFE_MERGE_SEPARATOR = " / "
LEGACY_MERGE_SEPARATOR = " "
ADVICE_KEYWORDS = ("надо", "нужно", "попробуй", "лучше", "сделай", "стоит")
FILTERED_PLACEHOLDER = "[FILTERED]"
FILTERED_PLACEHOLDER_PATTERN = re.compile(r"(?:\[\s*FILTERED\s*\](?:[\s,;/:-]+)?){2,}", flags=re.IGNORECASE)
FILTERED_ONLY_TEXT_PATTERN = re.compile(
    r"^(?:\[\s*FILTERED\s*\]|[\s,;/:\-()]|и|или|с|and|or)+$",
    flags=re.IGNORECASE,
)
FORBIDDEN_TOPIC_PATTERNS = (
    re.compile(r"\b(?:сво|svo|спецоперац\w*)\b", flags=re.IGNORECASE),
    re.compile(r"\bспециальн\w*\s+военн\w*\s+операц\w*\b", flags=re.IGNORECASE),
    re.compile(r"\b(?:наци\w*|нацизм\w*|фаш\w*|фашизм\w*|гитлер\w*|гитлера\w*)\b", flags=re.IGNORECASE),
)


@dataclass(slots=True)
class SummaryMessage:
    ts: float
    author_name: str
    message_text: str
    author_id: int | None = None
    author_username: str | None = None
    reply_to_user_id: int | None = None
    reply_to_username: str | None = None
    reply_to_name: str | None = None
    message_id: int | None = None


@dataclass(slots=True)
class PreparedSummaryContext:
    rendered_text: str
    raw_message_count: int
    turn_count: int
    merged_count: int


def prepare_summary_context(
    history_items: list[dict[str, Any]],
    anonymizer: "Anonymizer",
    enable_v2: bool,
) -> PreparedSummaryContext:
    normalized_messages = [
        message for message in normalize_summary_messages(history_items) if not is_bot_author(message.author_name)
    ]
    raw_message_count = len(normalized_messages)
    if not normalized_messages:
        return PreparedSummaryContext(
            rendered_text="",
            raw_message_count=0,
            turn_count=0,
            merged_count=0,
        )

    if enable_v2:
        merged_messages = merge_summary_messages(normalized_messages)
        role_tags = build_role_tags(merged_messages)
        rendered_text = anonymizer.render_messages_for_llm(merged_messages, role_tags=role_tags)
    else:
        merged_messages = merge_summary_messages(normalized_messages, safe_mode=False)
        rendered_text = anonymizer.render_messages_for_llm(merged_messages)

    return PreparedSummaryContext(
        rendered_text=rendered_text,
        raw_message_count=raw_message_count,
        turn_count=len(merged_messages),
        merged_count=max(raw_message_count - len(merged_messages), 0),
    )


def normalize_summary_messages(history_items: list[dict[str, Any]]) -> list[SummaryMessage]:
    normalized_messages: list[SummaryMessage] = []

    for item in history_items:
        message = normalize_summary_message(item)
        if message is None:
            logger.debug("Skipping malformed summary history item: %r", item)
            continue
        normalized_messages.append(message)

    return normalized_messages


def normalize_summary_message(item: dict[str, Any]) -> SummaryMessage | None:
    ts = item.get("ts")
    if not isinstance(ts, int | float):
        return None

    message_text = item.get("message_text")
    author_name = item.get("author_name")
    if isinstance(message_text, str) and isinstance(author_name, str):
        return SummaryMessage(
            ts=float(ts),
            author_name=author_name.strip(),
            message_text=sanitize_summary_message_text(message_text.strip()),
            author_id=_normalize_optional_int(item.get("author_id")),
            author_username=_normalize_optional_str(item.get("author_username")),
            reply_to_user_id=_normalize_optional_int(item.get("reply_to_user_id")),
            reply_to_username=_normalize_optional_str(item.get("reply_to_username")),
            reply_to_name=_normalize_optional_str(item.get("reply_to_name")),
            message_id=_normalize_optional_int(item.get("message_id")),
        )

    legacy_text = item.get("text")
    if not isinstance(legacy_text, str):
        return None

    parsed_legacy = parse_history_text(legacy_text)
    if parsed_legacy is None:
        return None

    return SummaryMessage(
        ts=float(ts),
        author_name=(parsed_legacy["author_name"] or "").strip(),
        message_text=sanitize_summary_message_text((parsed_legacy["message_text"] or "").strip()),
        author_username=_normalize_optional_str(parsed_legacy["author_username"]),
        reply_to_name=_normalize_optional_str(parsed_legacy["reply_to_name"]),
        message_id=_normalize_optional_int(item.get("message_id")),
    )


def sanitize_summary_message_text(text: str) -> str:
    sanitized = text
    for pattern in FORBIDDEN_TOPIC_PATTERNS:
        sanitized = pattern.sub(f" {FILTERED_PLACEHOLDER} ", sanitized)

    sanitized = FILTERED_PLACEHOLDER_PATTERN.sub(FILTERED_PLACEHOLDER, sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if FILTERED_ONLY_TEXT_PATTERN.fullmatch(sanitized):
        return FILTERED_PLACEHOLDER
    return sanitized


def merge_summary_messages(
    messages: list[SummaryMessage],
    *,
    safe_mode: bool = True,
) -> list[SummaryMessage]:
    if not messages:
        return []

    separator = SAFE_MERGE_SEPARATOR if safe_mode else LEGACY_MERGE_SEPARATOR
    merged_messages: list[SummaryMessage] = [messages[0]]

    for message in messages[1:]:
        previous = merged_messages[-1]
        if should_merge_messages(previous, message, safe_mode=safe_mode):
            merged_messages[-1] = SummaryMessage(
                ts=message.ts,
                author_name=previous.author_name,
                author_id=previous.author_id,
                author_username=previous.author_username,
                reply_to_user_id=previous.reply_to_user_id,
                reply_to_username=previous.reply_to_username,
                reply_to_name=previous.reply_to_name,
                message_id=previous.message_id,
                message_text=f"{previous.message_text}{separator}{message.message_text}".strip(),
            )
            continue
        merged_messages.append(message)

    return merged_messages


def should_merge_messages(previous: SummaryMessage, current: SummaryMessage, *, safe_mode: bool) -> bool:
    if get_message_identity_key(previous) != get_message_identity_key(current):
        return False
    if current.ts - previous.ts >= int(config.ChatConfig.AGGREGATE_WINDOW_SEC):
        return False
    if not safe_mode:
        return True
    if previous.reply_to_name or current.reply_to_name:
        return False

    previous_length = len(previous.message_text.strip())
    current_length = len(current.message_text.strip())
    return previous_length <= SAFE_MERGE_MAX_CHARS and current_length <= SAFE_MERGE_MAX_CHARS


def get_message_identity_key(message: SummaryMessage) -> str:
    if message.author_id is not None:
        return f"id:{message.author_id}"
    if message.author_username:
        return f"username:{message.author_username.lower()}"
    return f"name:{message.author_name}"


def build_role_tags(messages: list[SummaryMessage]) -> dict[str, str]:
    if not messages:
        return {}

    initiator_key = get_message_identity_key(messages[0])
    per_author_messages: dict[str, list[SummaryMessage]] = {}
    for message in messages:
        per_author_messages.setdefault(get_message_identity_key(message), []).append(message)

    role_tags: dict[str, str] = {}
    for author_key, author_messages in per_author_messages.items():
        lower_texts = [message.message_text.lower() for message in author_messages]
        if any(any(keyword in text for keyword in ADVICE_KEYWORDS) for text in lower_texts):
            role_tags[author_key] = "советует"
            continue
        if any(message.reply_to_name for message in author_messages):
            role_tags[author_key] = "отвечает"
            continue
        if any("?" in message.message_text for message in author_messages):
            role_tags[author_key] = "уточняет"
            continue

        average_length = sum(len(message.message_text.strip()) for message in author_messages) / len(author_messages)
        if average_length <= 40:
            role_tags[author_key] = "реагирует"
            continue
        if author_key == initiator_key:
            role_tags[author_key] = "инициатор"
            continue
        role_tags[author_key] = "комментирует"

    return role_tags


def is_bot_author(author_name: str) -> bool:
    normalized_name = author_name.strip().lower()
    return normalized_name == "bot" or "bot" in normalized_name


def _normalize_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_optional_int(value: Any) -> int | None:
    if not isinstance(value, int):
        return None
    return value
