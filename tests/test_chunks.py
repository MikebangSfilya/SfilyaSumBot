import json
from types import SimpleNamespace

import pytest

from sumbot.chunks import (
    CHUNK_QUEUE_KEY,
    append_message_to_active_chunk,
    build_chunk_status_key,
    fetch_chunk_summary_records,
)
from sumbot.chunk_worker import process_chunk_summary_job


class FakeRedis:
    def __init__(self):
        self.storage = {}
        self.lists = {}
        self.counters = {}

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

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

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


def make_history_payload(index: int) -> dict:
    return {
        "ts": float(index),
        "author_id": 7,
        "author_name": "Alice",
        "author_username": "alice",
        "reply_to_user_id": None,
        "reply_to_username": None,
        "reply_to_name": None,
        "message_text": f"message {index}",
        "text": f"message {index}",
        "message_id": index,
    }


async def build_closed_chunk(redis: FakeRedis, chat_id: int = 42):
    closed_chunk = None
    for index in range(50):
        closed_chunk = await append_message_to_active_chunk(redis, chat_id, make_history_payload(index))
    assert closed_chunk is not None
    return closed_chunk


@pytest.mark.asyncio
async def test_process_chunk_summary_job_saves_summary(monkeypatch):
    redis = FakeRedis()
    closed_chunk = await build_closed_chunk(redis)
    services = SimpleNamespace(
        redis=redis,
        get_chunk_llm_model=lambda: _async_result(
            SimpleNamespace(
                option=SimpleNamespace(model_name="deepseek-v4-flash", provider="DeepSeek API"),
                client=object(),
            )
        ),
    )

    async def fake_generate_chunk_summary(*args, **kwargs):
        return SimpleNamespace(
            payload={
                "topics": ["topic"],
                "events": [{"speaker_ref": "speaker_id_7", "text": "Alice shared message 1-50"}],
                "open_loops": [],
            }
        )

    monkeypatch.setattr("sumbot.chunk_worker.generate_chunk_summary", fake_generate_chunk_summary)

    result = await process_chunk_summary_job(
        services,
        chat_id=42,
        chunk_id=closed_chunk.chunk_id,
        attempts=0,
        system_prompt="prompt",
    )

    records = await fetch_chunk_summary_records(redis, 42)

    assert result == "saved"
    assert len(records) == 1
    assert records[0].chunk_id == "42-1"
    assert records[0].events[0].text == "Alice shared message 1-50"


@pytest.mark.asyncio
async def test_process_chunk_summary_job_is_idempotent_when_summary_already_exists(monkeypatch):
    redis = FakeRedis()
    closed_chunk = await build_closed_chunk(redis)
    services = SimpleNamespace(
        redis=redis,
        get_chunk_llm_model=lambda: _async_result(
            SimpleNamespace(
                option=SimpleNamespace(model_name="deepseek-v4-flash", provider="DeepSeek API"),
                client=object(),
            )
        ),
    )

    async def fake_generate_chunk_summary(*args, **kwargs):
        return SimpleNamespace(
            payload={
                "topics": ["topic"],
                "events": [{"speaker_ref": "speaker_id_7", "text": "once"}],
                "open_loops": [],
            }
        )

    monkeypatch.setattr("sumbot.chunk_worker.generate_chunk_summary", fake_generate_chunk_summary)

    first_result = await process_chunk_summary_job(
        services,
        chat_id=42,
        chunk_id=closed_chunk.chunk_id,
        attempts=0,
        system_prompt="prompt",
    )
    second_result = await process_chunk_summary_job(
        services,
        chat_id=42,
        chunk_id=closed_chunk.chunk_id,
        attempts=0,
        system_prompt="prompt",
    )

    assert first_result == "saved"
    assert second_result == "exists"


@pytest.mark.asyncio
async def test_process_chunk_summary_job_requeues_on_failure(monkeypatch):
    redis = FakeRedis()
    closed_chunk = await build_closed_chunk(redis)
    services = SimpleNamespace(
        redis=redis,
        get_chunk_llm_model=lambda: _async_result(
            SimpleNamespace(
                option=SimpleNamespace(model_name="deepseek-v4-flash", provider="DeepSeek API"),
                client=object(),
            )
        ),
    )

    async def fake_generate_chunk_summary(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("sumbot.chunk_worker.generate_chunk_summary", fake_generate_chunk_summary)

    result = await process_chunk_summary_job(
        services,
        chat_id=42,
        chunk_id=closed_chunk.chunk_id,
        attempts=0,
        system_prompt="prompt",
    )

    assert result == "requeued"
    assert redis.lists[CHUNK_QUEUE_KEY][-1] == '{"chat_id": 42, "chunk_id": "42-1", "attempts": 1}'
    assert redis.storage[build_chunk_status_key(42)] == "retry:42-1:1"


@pytest.mark.asyncio
async def test_process_chunk_summary_job_marks_terminal_failure_after_last_attempt(monkeypatch):
    redis = FakeRedis()
    closed_chunk = await build_closed_chunk(redis)
    services = SimpleNamespace(
        redis=redis,
        get_chunk_llm_model=lambda: _async_result(
            SimpleNamespace(
                option=SimpleNamespace(model_name="deepseek-v4-flash", provider="DeepSeek API"),
                client=object(),
            )
        ),
    )

    async def fake_generate_chunk_summary(*args, **kwargs):
        return SimpleNamespace(
            payload={
                "topics": ["topic"],
                "events": [{"speaker_ref": "unknown_speaker", "text": "bad"}],
                "open_loops": [],
            }
        )

    monkeypatch.setattr("sumbot.chunk_worker.generate_chunk_summary", fake_generate_chunk_summary)

    result = await process_chunk_summary_job(
        services,
        chat_id=42,
        chunk_id=closed_chunk.chunk_id,
        attempts=2,
        system_prompt="prompt",
    )

    assert result == "failed"
    assert redis.storage[build_chunk_status_key(42)] == "failed:42-1"


@pytest.mark.asyncio
async def test_process_chunk_summary_job_handles_missing_payload():
    redis = FakeRedis()
    services = SimpleNamespace(
        redis=redis,
        get_chunk_llm_model=lambda: _async_result(
            SimpleNamespace(
                option=SimpleNamespace(model_name="deepseek-v4-flash", provider="DeepSeek API"),
                client=object(),
            )
        ),
    )

    result = await process_chunk_summary_job(
        services,
        chat_id=42,
        chunk_id="42-404",
        attempts=0,
        system_prompt="prompt",
    )

    assert result == "missing_payload"
    assert redis.storage[build_chunk_status_key(42)] == "missing_payload:42-404"


async def _async_result(value):
    return value
