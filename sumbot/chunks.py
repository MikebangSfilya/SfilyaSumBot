from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from sumbot.constants import (
    CHUNK_MESSAGE_LIMIT,
    CHUNK_SUMMARY_RETENTION_LIMIT,
    DEFAULT_SUMMARY_PERIOD_SECONDS,
)

logger = logging.getLogger("SumBot.chunks")

CHUNK_QUEUE_KEY = "queue:chunk_summary"
CHUNKING_ENABLED_REDIS_KEY_PREFIX = "settings:chunking_enabled"
CHUNK_TIMELINE_STEP_SECONDS = 61.0


@dataclass(frozen=True, slots=True)
class ChunkParticipant:
    speaker_ref: str
    author_name: str
    author_id: int | None = None
    author_username: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkEvent:
    speaker_ref: str
    text: str
    reply_to_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkSummaryRecord:
    chat_id: int
    chunk_id: str
    message_count: int
    ts_from: float
    ts_to: float
    participants: tuple[ChunkParticipant, ...]
    topics: tuple[str, ...]
    events: tuple[ChunkEvent, ...]
    open_loops: tuple[str, ...]
    source_message_count: int


@dataclass(frozen=True, slots=True)
class ChunkRuntimeStats:
    active_chunk_size: int
    summarized_chunk_count: int
    last_status: str


@dataclass(frozen=True, slots=True)
class ClosedChunkPayload:
    chat_id: int
    chunk_id: str
    message_count: int
    ts_from: float
    ts_to: float
    messages: tuple[dict[str, Any], ...]
    attempts: int = 0


def build_chunking_enabled_key(chat_id: int) -> str:
    return f"{CHUNKING_ENABLED_REDIS_KEY_PREFIX}:{chat_id}"


def build_chunking_enabled_pattern() -> str:
    return f"{CHUNKING_ENABLED_REDIS_KEY_PREFIX}:*"


def build_active_chunk_key(chat_id: int) -> str:
    return f"chat:{chat_id}:chunk:active"


def build_chunk_index_key(chat_id: int) -> str:
    return f"chat:{chat_id}:chunk:index"


def build_chunk_summary_key(chat_id: int, chunk_id: str) -> str:
    return f"chat:{chat_id}:chunk:summary:{chunk_id}"


def build_chunk_payload_key(chat_id: int, chunk_id: str) -> str:
    return f"chat:{chat_id}:chunk:payload:{chunk_id}"


def build_chunk_counter_key(chat_id: int) -> str:
    return f"chat:{chat_id}:chunk:counter"


def build_chunk_status_key(chat_id: int) -> str:
    return f"chat:{chat_id}:chunk:status"


async def append_message_to_active_chunk(
    redis: Redis,
    chat_id: int,
    message_payload: dict[str, Any],
) -> ClosedChunkPayload | None:
    active_key = build_active_chunk_key(chat_id)
    raw_active = await redis.get(active_key)
    active_chunk = _load_json_object(raw_active)
    if active_chunk is None:
        active_chunk = await _build_new_active_chunk(redis, chat_id)

    messages = active_chunk.setdefault("messages", [])
    if not isinstance(messages, list):
        active_chunk = await _build_new_active_chunk(redis, chat_id)
        messages = active_chunk["messages"]

    messages.append(message_payload)
    active_chunk["updated_at"] = float(message_payload.get("ts", time.time()))

    if len(messages) < CHUNK_MESSAGE_LIMIT:
        await redis.set(active_key, json.dumps(active_chunk, ensure_ascii=True))
        return None

    chunk_id = str(active_chunk["chunk_id"])
    payload = ClosedChunkPayload(
        chat_id=chat_id,
        chunk_id=chunk_id,
        message_count=len(messages),
        ts_from=float(messages[0].get("ts", 0.0)),
        ts_to=float(messages[-1].get("ts", 0.0)),
        messages=tuple(messages),
        attempts=0,
    )
    await redis.set(build_chunk_payload_key(chat_id, chunk_id), json.dumps(_serialize_closed_chunk(payload), ensure_ascii=True))
    await redis.rpush(CHUNK_QUEUE_KEY, json.dumps({"chat_id": chat_id, "chunk_id": chunk_id, "attempts": 0}, ensure_ascii=True))
    await redis.set(build_chunk_status_key(chat_id), f"queued:{chunk_id}")
    next_active_chunk = await _build_new_active_chunk(redis, chat_id)
    await redis.set(active_key, json.dumps(next_active_chunk, ensure_ascii=True))
    logger.info(
        "Chunk rotated and enqueued (chat_id=%s, chunk_id=%s, message_count=%s)",
        chat_id,
        chunk_id,
        payload.message_count,
    )
    return payload


async def load_closed_chunk_payload(redis: Redis, chat_id: int, chunk_id: str) -> ClosedChunkPayload | None:
    raw_payload = await redis.get(build_chunk_payload_key(chat_id, chunk_id))
    payload_data = _load_json_object(raw_payload)
    if payload_data is None:
        return None
    messages = payload_data.get("messages")
    if not isinstance(messages, list):
        return None
    return ClosedChunkPayload(
        chat_id=int(payload_data.get("chat_id", chat_id)),
        chunk_id=str(payload_data.get("chunk_id", chunk_id)),
        message_count=int(payload_data.get("message_count", len(messages))),
        ts_from=float(payload_data.get("ts_from", 0.0)),
        ts_to=float(payload_data.get("ts_to", 0.0)),
        messages=tuple(item for item in messages if isinstance(item, dict)),
        attempts=int(payload_data.get("attempts", 0)),
    )


async def save_chunk_summary_record(redis: Redis, record: ChunkSummaryRecord) -> None:
    await redis.set(
        build_chunk_summary_key(record.chat_id, record.chunk_id),
        json.dumps(_serialize_chunk_summary(record), ensure_ascii=True),
    )
    index_key = build_chunk_index_key(record.chat_id)
    existing_ids = await redis.lrange(index_key, 0, -1)
    if record.chunk_id not in existing_ids:
        await redis.rpush(index_key, record.chunk_id)
    while await redis.llen(index_key) > CHUNK_SUMMARY_RETENTION_LIMIT:
        removed_chunk_id = await redis.lpop(index_key)
        if removed_chunk_id:
            await redis.delete(build_chunk_summary_key(record.chat_id, str(removed_chunk_id)))
    await redis.delete(build_chunk_payload_key(record.chat_id, record.chunk_id))
    await redis.set(build_chunk_status_key(record.chat_id), f"ready:{record.chunk_id}")
    logger.info(
        "Chunk summary saved (chat_id=%s, chunk_id=%s, events=%s, topics=%s)",
        record.chat_id,
        record.chunk_id,
        len(record.events),
        len(record.topics),
    )


async def requeue_chunk_summary_job(redis: Redis, chat_id: int, chunk_id: str, attempts: int, status: str) -> None:
    payload = await load_closed_chunk_payload(redis, chat_id, chunk_id)
    if payload is not None:
        updated_payload = ClosedChunkPayload(
            chat_id=payload.chat_id,
            chunk_id=payload.chunk_id,
            message_count=payload.message_count,
            ts_from=payload.ts_from,
            ts_to=payload.ts_to,
            messages=payload.messages,
            attempts=attempts,
        )
        await redis.set(
            build_chunk_payload_key(chat_id, chunk_id),
            json.dumps(_serialize_closed_chunk(updated_payload), ensure_ascii=True),
        )
    await redis.rpush(
        CHUNK_QUEUE_KEY,
        json.dumps({"chat_id": chat_id, "chunk_id": chunk_id, "attempts": attempts}, ensure_ascii=True),
    )
    await redis.set(build_chunk_status_key(chat_id), f"{status}:{chunk_id}:{attempts}")


async def mark_chunk_summary_terminal_failure(redis: Redis, chat_id: int, chunk_id: str, status: str) -> None:
    await redis.set(build_chunk_status_key(chat_id), f"{status}:{chunk_id}")


async def get_chunk_runtime_stats(redis: Redis, chat_id: int) -> ChunkRuntimeStats:
    raw_active = await redis.get(build_active_chunk_key(chat_id))
    active_data = _load_json_object(raw_active)
    active_messages = active_data.get("messages", []) if isinstance(active_data, dict) else []
    active_chunk_size = len(active_messages) if isinstance(active_messages, list) else 0

    summarized_chunk_count = await redis.llen(build_chunk_index_key(chat_id))
    last_status = await redis.get(build_chunk_status_key(chat_id)) or "idle"
    if isinstance(last_status, bytes):
        last_status = last_status.decode()

    return ChunkRuntimeStats(
        active_chunk_size=active_chunk_size,
        summarized_chunk_count=int(summarized_chunk_count),
        last_status=str(last_status),
    )


async def fetch_chunk_summary_records(redis: Redis, chat_id: int) -> list[ChunkSummaryRecord]:
    chunk_ids = await redis.lrange(build_chunk_index_key(chat_id), 0, -1)
    records: list[ChunkSummaryRecord] = []
    for chunk_id in chunk_ids:
        raw_summary = await redis.get(build_chunk_summary_key(chat_id, str(chunk_id)))
        record = parse_chunk_summary_record(chat_id, str(chunk_id), raw_summary)
        if record is not None:
            records.append(record)
    return records


def parse_chunk_summary_record(chat_id: int, chunk_id: str, raw_summary: str | bytes | None) -> ChunkSummaryRecord | None:
    data = _load_json_object(raw_summary)
    if data is None:
        return None

    raw_participants = data.get("participants")
    raw_events = data.get("events")
    if not isinstance(raw_participants, list) or not isinstance(raw_events, list):
        return None

    participants = []
    for item in raw_participants:
        if not isinstance(item, dict):
            continue
        speaker_ref = item.get("speaker_ref")
        author_name = item.get("author_name")
        if not isinstance(speaker_ref, str) or not isinstance(author_name, str):
            continue
        participants.append(
            ChunkParticipant(
                speaker_ref=speaker_ref,
                author_name=author_name,
                author_id=item.get("author_id") if isinstance(item.get("author_id"), int) else None,
                author_username=item.get("author_username") if isinstance(item.get("author_username"), str) else None,
            )
        )

    events = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        speaker_ref = item.get("speaker_ref")
        text = item.get("text")
        reply_to_ref = item.get("reply_to_ref")
        if not isinstance(speaker_ref, str) or not isinstance(text, str):
            continue
        events.append(
            ChunkEvent(
                speaker_ref=speaker_ref,
                text=text.strip(),
                reply_to_ref=reply_to_ref if isinstance(reply_to_ref, str) and reply_to_ref else None,
            )
        )

    if not participants or not events:
        return None

    return ChunkSummaryRecord(
        chat_id=int(data.get("chat_id", chat_id)),
        chunk_id=str(data.get("chunk_id", chunk_id)),
        message_count=int(data.get("message_count", 0) or 0),
        ts_from=float(data.get("ts_from", 0.0) or 0.0),
        ts_to=float(data.get("ts_to", 0.0) or 0.0),
        participants=tuple(participants),
        topics=tuple(item for item in data.get("topics", []) if isinstance(item, str) and item.strip()),
        events=tuple(events),
        open_loops=tuple(item for item in data.get("open_loops", []) if isinstance(item, str) and item.strip()),
        source_message_count=int(data.get("source_message_count", 0) or 0),
    )


async def fetch_chunk_backfill_messages(
    redis: Redis,
    chat_id: int,
    *,
    raw_messages: list[dict[str, Any]],
    limit_messages: int | None = None,
    time_limit_seconds: int | None = None,
    current_ts: float | None = None,
) -> list[dict[str, Any]]:
    summaries = await fetch_chunk_summary_records(redis, chat_id)
    if not summaries:
        return []

    raw_oldest_ts = _get_oldest_ts(raw_messages)
    selected: list[ChunkSummaryRecord] = []
    if limit_messages is not None:
        covered_messages = len(raw_messages)
        if covered_messages >= limit_messages:
            return []
        for record in reversed(summaries):
            if raw_oldest_ts is not None and record.ts_to >= raw_oldest_ts:
                continue
            selected.append(record)
            covered_messages += max(record.source_message_count, record.message_count, 0)
            if covered_messages >= limit_messages:
                break
        selected.reverse()
    else:
        now_ts = current_ts if current_ts is not None else time.time()
        cutoff_ts = now_ts - (time_limit_seconds or DEFAULT_SUMMARY_PERIOD_SECONDS)
        for record in summaries:
            if record.ts_to < cutoff_ts:
                continue
            if raw_oldest_ts is not None and record.ts_to >= raw_oldest_ts:
                continue
            selected.append(record)

    if not selected:
        return []
    return convert_chunk_summaries_to_history_items(selected, end_before_ts=raw_oldest_ts)


def convert_chunk_summaries_to_history_items(
    summaries: list[ChunkSummaryRecord],
    *,
    end_before_ts: float | None = None,
) -> list[dict[str, Any]]:
    if not summaries:
        return []

    total_events = sum(len(record.events) for record in summaries)
    if total_events <= 0:
        return []

    if end_before_ts is not None:
        current_ts = end_before_ts - (total_events * CHUNK_TIMELINE_STEP_SECONDS)
    else:
        current_ts = min(record.ts_from for record in summaries)

    items: list[dict[str, Any]] = []
    for record in summaries:
        participants_by_ref = {participant.speaker_ref: participant for participant in record.participants}
        for event in record.events:
            participant = participants_by_ref.get(event.speaker_ref)
            if participant is None:
                continue
            reply_participant = participants_by_ref.get(event.reply_to_ref or "")
            items.append(
                {
                    "ts": current_ts,
                    "author_name": participant.author_name,
                    "author_id": participant.author_id,
                    "author_username": participant.author_username,
                    "reply_to_user_id": reply_participant.author_id if reply_participant else None,
                    "reply_to_username": reply_participant.author_username if reply_participant else None,
                    "reply_to_name": reply_participant.author_name if reply_participant else None,
                    "message_text": event.text,
                    "message_id": None,
                }
            )
            current_ts += CHUNK_TIMELINE_STEP_SECONDS
    return items


def build_chunk_source_payload(messages: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    participants: dict[str, dict[str, Any]] = {}
    rendered_messages = []

    for item in messages:
        speaker_ref = build_stable_speaker_ref(item)
        participants.setdefault(
            speaker_ref,
            {
                "speaker_ref": speaker_ref,
                "author_id": item.get("author_id") if isinstance(item.get("author_id"), int) else None,
                "author_username": item.get("author_username") if isinstance(item.get("author_username"), str) else None,
                "author_name": item.get("author_name") if isinstance(item.get("author_name"), str) else "Unknown",
            },
        )
        reply_to_ref = build_reply_speaker_ref(item)
        if reply_to_ref:
            participants.setdefault(
                reply_to_ref,
                {
                    "speaker_ref": reply_to_ref,
                    "author_id": item.get("reply_to_user_id") if isinstance(item.get("reply_to_user_id"), int) else None,
                    "author_username": item.get("reply_to_username") if isinstance(item.get("reply_to_username"), str) else None,
                    "author_name": item.get("reply_to_name") if isinstance(item.get("reply_to_name"), str) else reply_to_ref,
                },
            )
        rendered_messages.append(
            {
                "ts": float(item.get("ts", 0.0)),
                "speaker_ref": speaker_ref,
                "reply_to_ref": reply_to_ref,
                "text": item.get("message_text") if isinstance(item.get("message_text"), str) else item.get("text", ""),
            }
        )

    return {
        "participants": [
            {
                "speaker_ref": item["speaker_ref"],
                "author_name": item["author_name"],
            }
            for item in participants.values()
        ],
        "messages": rendered_messages,
    }


def build_chunk_summary_record(
    payload: ClosedChunkPayload,
    summary_payload: dict[str, Any],
) -> ChunkSummaryRecord:
    source_participants = _build_participants_from_messages(payload.messages)
    source_participants_by_ref = {item.speaker_ref: item for item in source_participants}
    topics = tuple(_normalize_compact_list(summary_payload.get("topics")))
    open_loops = tuple(_normalize_compact_list(summary_payload.get("open_loops")))

    raw_events = summary_payload.get("events")
    events: list[ChunkEvent] = []
    if isinstance(raw_events, list):
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            speaker_ref = item.get("speaker_ref")
            text = item.get("text")
            reply_to_ref = item.get("reply_to_ref")
            if (
                isinstance(speaker_ref, str)
                and speaker_ref in source_participants_by_ref
                and isinstance(text, str)
                and text.strip()
            ):
                normalized_reply = reply_to_ref if isinstance(reply_to_ref, str) and reply_to_ref in source_participants_by_ref else None
                events.append(ChunkEvent(speaker_ref=speaker_ref, text=text.strip(), reply_to_ref=normalized_reply))

    if not events:
        raise ValueError("Chunk summary payload does not contain valid events")

    return ChunkSummaryRecord(
        chat_id=payload.chat_id,
        chunk_id=payload.chunk_id,
        message_count=payload.message_count,
        ts_from=payload.ts_from,
        ts_to=payload.ts_to,
        participants=tuple(source_participants),
        topics=topics,
        events=tuple(events),
        open_loops=open_loops,
        source_message_count=payload.message_count,
    )


def build_stable_speaker_ref(item: dict[str, Any]) -> str:
    author_id = item.get("author_id")
    if isinstance(author_id, int):
        return f"speaker_id_{author_id}"

    author_username = item.get("author_username")
    if isinstance(author_username, str) and author_username.strip():
        normalized = author_username.strip().lower().replace(" ", "_")
        return f"speaker_username_{normalized}"

    author_name = item.get("author_name")
    normalized_name = (author_name.strip().lower().replace(" ", "_") if isinstance(author_name, str) and author_name.strip() else "unknown")
    return f"speaker_name_{normalized_name}"


def build_reply_speaker_ref(item: dict[str, Any]) -> str | None:
    reply_to_user_id = item.get("reply_to_user_id")
    if isinstance(reply_to_user_id, int):
        return f"speaker_id_{reply_to_user_id}"

    reply_to_username = item.get("reply_to_username")
    if isinstance(reply_to_username, str) and reply_to_username.strip():
        normalized = reply_to_username.strip().lower().replace(" ", "_")
        return f"speaker_username_{normalized}"

    reply_to_name = item.get("reply_to_name")
    if isinstance(reply_to_name, str) and reply_to_name.strip():
        normalized = reply_to_name.strip().lower().replace(" ", "_")
        return f"speaker_name_{normalized}"

    return None


def _build_participants_from_messages(messages: tuple[dict[str, Any], ...]) -> list[ChunkParticipant]:
    participants: dict[str, ChunkParticipant] = {}
    for item in messages:
        speaker_ref = build_stable_speaker_ref(item)
        participants.setdefault(
            speaker_ref,
            ChunkParticipant(
                speaker_ref=speaker_ref,
                author_name=item.get("author_name") if isinstance(item.get("author_name"), str) else "Unknown",
                author_id=item.get("author_id") if isinstance(item.get("author_id"), int) else None,
                author_username=item.get("author_username") if isinstance(item.get("author_username"), str) else None,
            ),
        )
        reply_ref = build_reply_speaker_ref(item)
        if reply_ref and reply_ref not in participants:
            participants[reply_ref] = ChunkParticipant(
                speaker_ref=reply_ref,
                author_name=item.get("reply_to_name") if isinstance(item.get("reply_to_name"), str) else reply_ref,
                author_id=item.get("reply_to_user_id") if isinstance(item.get("reply_to_user_id"), int) else None,
                author_username=item.get("reply_to_username") if isinstance(item.get("reply_to_username"), str) else None,
            )
    return list(participants.values())


async def _build_new_active_chunk(redis: Redis, chat_id: int) -> dict[str, Any]:
    chunk_number = await redis.incr(build_chunk_counter_key(chat_id))
    return {
        "chunk_id": f"{chat_id}-{chunk_number}",
        "messages": [],
        "created_at": time.time(),
        "updated_at": time.time(),
    }


def _load_json_object(value: str | bytes | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode()
    if not isinstance(value, str) or not value:
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _serialize_closed_chunk(payload: ClosedChunkPayload) -> dict[str, Any]:
    return {
        "chat_id": payload.chat_id,
        "chunk_id": payload.chunk_id,
        "message_count": payload.message_count,
        "ts_from": payload.ts_from,
        "ts_to": payload.ts_to,
        "messages": list(payload.messages),
        "attempts": payload.attempts,
    }


def _serialize_chunk_summary(record: ChunkSummaryRecord) -> dict[str, Any]:
    return {
        "chat_id": record.chat_id,
        "chunk_id": record.chunk_id,
        "message_count": record.message_count,
        "ts_from": record.ts_from,
        "ts_to": record.ts_to,
        "participants": [
            {
                "speaker_ref": participant.speaker_ref,
                "author_name": participant.author_name,
                "author_id": participant.author_id,
                "author_username": participant.author_username,
            }
            for participant in record.participants
        ],
        "topics": list(record.topics),
        "events": [
            {
                "speaker_ref": event.speaker_ref,
                "reply_to_ref": event.reply_to_ref,
                "text": event.text,
            }
            for event in record.events
        ],
        "open_loops": list(record.open_loops),
        "source_message_count": record.source_message_count,
    }


def _normalize_compact_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _get_oldest_ts(messages: list[dict[str, Any]]) -> float | None:
    timestamps = [float(item["ts"]) for item in messages if isinstance(item.get("ts"), int | float)]
    if not timestamps:
        return None
    return min(timestamps)
