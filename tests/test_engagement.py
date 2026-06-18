import json
from types import SimpleNamespace

import pytest

import config
from sumbot.engagement import (
    build_last_manual_summary_key,
    build_onboarding_pending_key,
    maybe_send_onboarding_ready_hint,
    record_manual_summary_request,
    start_chat_onboarding,
    was_manual_summary_requested_recently,
)
from sumbot.history import build_chat_history_key


class FakeRedis:
    def __init__(self):
        self.storage = {}
        self.lists = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.storage:
            return False
        self.storage[key] = value
        return True

    async def get(self, key):
        return self.storage.get(key)

    async def delete(self, key):
        self.storage.pop(key, None)

    async def llen(self, key):
        return len(self.lists.get(key, []))


class FakeMessage:
    def __init__(self, chat_id=42):
        self.chat = SimpleNamespace(id=chat_id, type="supergroup")
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


@pytest.mark.asyncio
async def test_manual_summary_suppression_includes_one_hour_boundary():
    redis = FakeRedis()
    await record_manual_summary_request(redis, 42, requested_at=1_000.0)

    assert await was_manual_summary_requested_recently(
        redis,
        42,
        current_time=4_600.0,
        window_seconds=3_600,
    )
    assert not await was_manual_summary_requested_recently(
        redis,
        42,
        current_time=4_601.0,
        window_seconds=3_600,
    )
    assert build_last_manual_summary_key(42) in redis.storage


@pytest.mark.asyncio
async def test_onboarding_hint_is_sent_once_after_message_threshold(monkeypatch):
    redis = FakeRedis()
    message = FakeMessage()
    monkeypatch.setattr(config, "ONBOARDING_READY_MESSAGE_COUNT", 3)
    await start_chat_onboarding(redis, message.chat.id)
    redis.lists[build_chat_history_key(message.chat.id)] = [json.dumps({"ts": index}) for index in range(3)]

    assert await maybe_send_onboarding_ready_hint(redis, message)
    assert not await maybe_send_onboarding_ready_hint(redis, message)
    assert len(message.answers) == 1
    assert build_onboarding_pending_key(message.chat.id) in redis.storage
