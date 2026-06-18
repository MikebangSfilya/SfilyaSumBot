import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sumbot.chunks import (
    CHUNK_QUEUE_KEY,
    append_message_to_active_chunk,
    fetch_chunk_backfill_messages,
    save_chunk_summary_record,
    ChunkEvent,
    ChunkParticipant,
    ChunkSummaryRecord,
)
import sumbot.history as history
from sumbot.history import (
    build_chat_history_key,
    build_history_message_payload,
    fetch_messages_for_summary,
    format_message_for_history,
    parse_history_text,
    save_message_to_history,
)


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.storage = {}
        self.counters = {}

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def ltrim(self, key, start, end):
        values = self.lists.get(key, [])
        if end == -1:
            self.lists[key] = values[start:]
        else:
            self.lists[key] = values[start : end + 1]

    async def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def lpop(self, key):
        values = self.lists.get(key, [])
        if not values:
            return None
        return values.pop(0)

    async def get(self, key):
        return self.storage.get(key)

    async def set(self, key, value, **kwargs):
        self.storage[key] = value
        return True

    async def delete(self, key):
        self.storage.pop(key, None)

    async def incr(self, key):
        next_value = int(self.counters.get(key, 0)) + 1
        self.counters[key] = next_value
        return next_value


def make_message(
    text,
    timestamp,
    chat_id=42,
    first_name="Alice",
    username="alice",
    user_id=7,
    reply_to_user=None,
):
    return SimpleNamespace(
        text=text,
        date=datetime.fromtimestamp(timestamp, tz=timezone.utc),
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username=username),
        reply_to_message=SimpleNamespace(from_user=reply_to_user) if reply_to_user else None,
    )


def test_format_message_for_history_includes_user_chat_time_and_text():
    timestamp = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc).timestamp()
    message = make_message("hello", timestamp)

    assert format_message_for_history(message) == "[01.05 12:30] Alice (@alice): hello"


@pytest.mark.asyncio
async def test_save_and_fetch_messages_for_summary_returns_chronological_rows():
    redis = FakeRedis()

    await save_message_to_history(redis, make_message("old", 100.0))
    await save_message_to_history(redis, make_message("new", 200.0, first_name="Bob", username="bob"))

    messages = await fetch_messages_for_summary(redis, chat_id=42, limit_messages=2)

    assert [item["ts"] for item in messages] == [100.0, 200.0]
    assert messages[0]["text"].endswith("old")
    assert messages[1]["text"].endswith("new")
    assert messages[0]["message_text"] == "old"
    assert messages[0]["author_id"] == 7
    assert messages[0]["author_name"] == "Alice"
    assert messages[1]["author_username"] == "bob"
    assert messages[1]["reply_to_name"] is None


@pytest.mark.asyncio
async def test_save_message_to_history_stores_reply_identity_metadata():
    redis = FakeRedis()
    reply_to_user = SimpleNamespace(id=20, first_name="Bob", username="bob")

    await save_message_to_history(
        redis,
        make_message("answer", 100.0, first_name="Alice", username="alice", user_id=10, reply_to_user=reply_to_user),
    )

    messages = await fetch_messages_for_summary(redis, chat_id=42, limit_messages=1)

    assert messages[0]["author_id"] == 10
    assert messages[0]["reply_to_user_id"] == 20
    assert messages[0]["reply_to_username"] == "bob"
    assert messages[0]["reply_to_name"] == "Bob"


@pytest.mark.asyncio
async def test_fetch_messages_for_summary_respects_time_limit_and_skip_markers(monkeypatch):
    redis = FakeRedis()
    key = build_chat_history_key(42)
    redis.lists[key] = [
        json.dumps({"ts": 995.0, "text": "fresh"}),
        json.dumps({"ts": 994.0, "text": "fresh /summary marker"}),
        json.dumps({"ts": 500.0, "text": "old"}),
    ]
    monkeypatch.setattr(history.time, "time", lambda: 1_000.0)

    messages = await fetch_messages_for_summary(redis, chat_id=42, time_limit_seconds=10)

    assert messages == [{"ts": 995.0, "text": "fresh"}]


def test_parse_history_text_extracts_structured_fields():
    parsed = parse_history_text("[01.05 12:30] Alice (@alice) (в ответ Bob): hello")

    assert parsed == {
        "author_name": "Alice",
        "author_username": "alice",
        "reply_to_name": "Bob",
        "message_text": "hello",
    }


def test_build_history_message_payload_preserves_structured_fields():
    timestamp = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc).timestamp()
    reply_to_user = SimpleNamespace(id=20, first_name="Bob", username="bob")
    payload = build_history_message_payload(make_message("hello", timestamp, reply_to_user=reply_to_user))

    assert payload == {
        "ts": timestamp,
        "text": "[01.05 12:30] Alice (@alice) (в ответ Bob): hello",
        "message_text": "hello",
        "author_id": 7,
        "author_name": "Alice",
        "author_username": "alice",
        "reply_to_user_id": 20,
        "reply_to_username": "bob",
        "reply_to_name": "Bob",
        "message_id": None,
    }


@pytest.mark.asyncio
async def test_append_message_to_active_chunk_rotates_on_fiftieth_message():
    redis = FakeRedis()
    closed_chunk = None

    for index in range(50):
        payload = {
            "ts": float(index),
            "author_id": 7,
            "author_name": "Alice",
            "author_username": "alice",
            "message_text": f"msg {index}",
            "text": f"msg {index}",
        }
        closed_chunk = await append_message_to_active_chunk(redis, 42, payload)

    assert closed_chunk is not None
    assert closed_chunk.message_count == 50
    assert closed_chunk.chunk_id == "42-1"
    assert redis.lists[CHUNK_QUEUE_KEY] == ['{"chat_id": 42, "chunk_id": "42-1", "attempts": 0}']


@pytest.mark.asyncio
async def test_save_chunk_summary_record_overwrites_oldest_on_eleventh_chunk():
    redis = FakeRedis()
    for index in range(11):
        record = ChunkSummaryRecord(
            chat_id=42,
            chunk_id=f"42-{index + 1}",
            message_count=50,
            ts_from=float(index * 100),
            ts_to=float(index * 100 + 10),
            participants=(ChunkParticipant(speaker_ref="speaker_id_7", author_name="Alice", author_id=7, author_username="alice"),),
            topics=(f"topic {index}",),
            events=(ChunkEvent(speaker_ref="speaker_id_7", text=f"event {index}"),),
            open_loops=(),
            source_message_count=50,
        )
        await save_chunk_summary_record(redis, record)

    assert redis.lists["chat:42:chunk:index"] == [f"42-{index}" for index in range(2, 12)]
    assert "chat:42:chunk:summary:42-1" not in redis.storage


@pytest.mark.asyncio
async def test_fetch_chunk_backfill_messages_uses_boundary_before_oldest_raw(monkeypatch):
    redis = FakeRedis()
    await save_chunk_summary_record(
        redis,
        ChunkSummaryRecord(
            chat_id=42,
            chunk_id="42-1",
            message_count=50,
            ts_from=100.0,
            ts_to=200.0,
            participants=(
                ChunkParticipant(
                    speaker_ref="speaker_id_7",
                    author_name="Alice",
                    author_id=7,
                    author_username="alice",
                ),
            ),
            topics=("topic",),
            events=(
                ChunkEvent(speaker_ref="speaker_id_7", text="older chunk event"),
            ),
            open_loops=(),
            source_message_count=50,
        ),
    )
    raw_messages = [
        {"ts": 300.0, "author_name": "Alice", "author_id": 7, "author_username": "alice", "message_text": "raw tail"},
    ]
    monkeypatch.setattr(history.time, "time", lambda: 1_000.0)

    backfill = await fetch_chunk_backfill_messages(
        redis,
        42,
        raw_messages=raw_messages,
        time_limit_seconds=24 * 3600,
        current_ts=1_000.0,
    )

    assert len(backfill) == 1
    assert backfill[0]["message_text"] == "older chunk event"
    assert backfill[0]["ts"] < raw_messages[0]["ts"]
