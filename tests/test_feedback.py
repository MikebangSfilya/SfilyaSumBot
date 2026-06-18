from types import SimpleNamespace

import pytest

import sumbot.feedback as feedback
from sumbot.feedback import (
    SUMMARY_FEEDBACK_CALLBACK_PREFIX,
    SUMMARY_FEEDBACK_DETAILS_CALLBACK_PREFIX,
    SUMMARY_FEEDBACK_OPTIONS,
    acquire_summary_feedback_rate_limit,
    build_summary_feedback_keyboard,
    build_pending_feedback_details_key,
    build_summary_feedback_rate_limit_key,
    clear_pending_feedback_details,
    get_pending_feedback_details,
    has_feedback_for_summary,
    normalize_feedback_details,
    parse_summary_feedback_callback,
    save_feedback_details_for_summary,
    save_feedback_for_summary,
    save_pending_feedback_details,
)


class FakeBegin:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeEngine:
    def __init__(self):
        self.conn = SimpleNamespace()

    def begin(self):
        return FakeBegin(self.conn)


class FakeRedis:
    def __init__(self):
        self.storage = {}
        self.expirations = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.storage:
            return False
        self.storage[key] = value
        self.expirations[key] = ex
        return True

    async def get(self, key):
        return self.storage.get(key)

    async def delete(self, key):
        self.storage.pop(key, None)
        self.expirations.pop(key, None)


def test_build_summary_feedback_keyboard_contains_all_options():
    keyboard = build_summary_feedback_keyboard()

    assert [
        button.callback_data for button in keyboard.inline_keyboard[0]
    ] == [
        f"{SUMMARY_FEEDBACK_CALLBACK_PREFIX}{feedback_value}"
        for feedback_value in SUMMARY_FEEDBACK_OPTIONS
    ]
    assert keyboard.inline_keyboard[1][0].text == "💬 Комментарий"
    assert keyboard.inline_keyboard[1][0].callback_data == SUMMARY_FEEDBACK_DETAILS_CALLBACK_PREFIX


def test_parse_summary_feedback_callback_returns_value_and_sentiment():
    assert parse_summary_feedback_callback("summary_feedback:good") == ("good", "positive")


def test_normalize_feedback_details_strips_and_limits_text():
    assert normalize_feedback_details("  не понял контекст  ") == "не понял контекст"
    assert len(normalize_feedback_details("x" * 1200)) == 1000


@pytest.mark.asyncio
async def test_pending_feedback_details_round_trip():
    redis = FakeRedis()

    await save_pending_feedback_details(
        redis,
        chat_id=42,
        telegram_message_id=7,
        user_id=11,
        prompt_message_id=99,
    )

    pending = await get_pending_feedback_details(redis, chat_id=42, user_id=11)

    assert pending is not None
    assert pending.chat_id == 42
    assert pending.telegram_message_id == 7
    assert pending.user_id == 11
    assert pending.prompt_message_id == 99
    assert redis.expirations[build_pending_feedback_details_key(42, 11)] == 15 * 60

    await clear_pending_feedback_details(redis, chat_id=42, user_id=11)

    assert await get_pending_feedback_details(redis, chat_id=42, user_id=11) is None


@pytest.mark.asyncio
async def test_summary_feedback_rate_limit_uses_redis_nx_key():
    redis = FakeRedis()

    first_acquired = await acquire_summary_feedback_rate_limit(
        redis,
        chat_id=42,
        telegram_message_id=7,
        user_id=11,
        action="rating",
    )
    second_acquired = await acquire_summary_feedback_rate_limit(
        redis,
        chat_id=42,
        telegram_message_id=7,
        user_id=11,
        action="rating",
    )

    key = build_summary_feedback_rate_limit_key(42, 7, 11, "rating")
    assert first_acquired is True
    assert second_acquired is False
    assert redis.storage[key] == "1"
    assert redis.expirations[key] == 5


@pytest.mark.asyncio
async def test_save_feedback_for_summary_upserts_when_summary_log_exists(monkeypatch):
    engine = FakeEngine()
    captured = {}

    async def fake_wait_for_summary_log_id(conn, chat_id, telegram_message_id):
        captured["lookup"] = (conn, chat_id, telegram_message_id)
        return 99

    async def fake_upsert_summary_feedback(
        conn,
        summary_log_id,
        chat_id,
        telegram_message_id,
        user_id,
        feedback_value,
        sentiment,
    ):
        captured["upsert"] = (
            conn,
            summary_log_id,
            chat_id,
            telegram_message_id,
            user_id,
            feedback_value,
            sentiment,
        )

    monkeypatch.setattr(feedback, "wait_for_summary_log_id", fake_wait_for_summary_log_id)
    monkeypatch.setattr(feedback, "upsert_summary_feedback", fake_upsert_summary_feedback)

    saved = await save_feedback_for_summary(
        engine,
        chat_id=42,
        telegram_message_id=7,
        user_id=11,
        feedback_value="good",
        sentiment="positive",
    )

    assert saved is True
    assert captured["lookup"] == (engine.conn, 42, 7)
    assert captured["upsert"] == (engine.conn, 99, 42, 7, 11, "good", "positive")


@pytest.mark.asyncio
async def test_save_feedback_details_for_summary_updates_existing_feedback(monkeypatch):
    engine = FakeEngine()
    captured = {}

    async def fake_update_summary_feedback_details(conn, chat_id, telegram_message_id, user_id, details):
        captured["details"] = (conn, chat_id, telegram_message_id, user_id, details)
        return True

    monkeypatch.setattr(
        feedback,
        "update_summary_feedback_details",
        fake_update_summary_feedback_details,
    )

    saved = await save_feedback_details_for_summary(
        engine,
        chat_id=42,
        telegram_message_id=7,
        user_id=11,
        details="не понял контекст",
    )

    assert saved is True
    assert captured["details"] == (engine.conn, 42, 7, 11, "не понял контекст")


@pytest.mark.asyncio
async def test_has_feedback_for_summary_checks_existing_feedback(monkeypatch):
    engine = FakeEngine()
    captured = {}

    async def fake_summary_feedback_exists(conn, chat_id, telegram_message_id, user_id):
        captured["lookup"] = (conn, chat_id, telegram_message_id, user_id)
        return True

    monkeypatch.setattr(feedback, "summary_feedback_exists", fake_summary_feedback_exists)

    exists = await has_feedback_for_summary(
        engine,
        chat_id=42,
        telegram_message_id=7,
        user_id=11,
    )

    assert exists is True
    assert captured["lookup"] == (engine.conn, 42, 7, 11)
