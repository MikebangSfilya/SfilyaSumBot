from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import config
from sumbot.daily_digest import (
    DAILY_DIGEST_DISABLED_CHATS_KEY,
    DAILY_DIGEST_ENABLED_CHATS_KEY,
    DailyDigestResult,
    build_daily_digest_schedule,
    calculate_next_daily_run,
    fetch_daily_digest_chat_ids,
    is_daily_digest_enabled,
    run_daily_digest_cycle,
    set_daily_digest_enabled,
)
from sumbot.engagement import record_manual_summary_request
from sumbot.telegram_handlers.digest import can_manage_daily_digest


class FakeRedis:
    def __init__(self):
        self.storage = {}
        self.sets = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.storage:
            return False
        self.storage[key] = value
        return True

    async def get(self, key):
        return self.storage.get(key)

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key, value):
        self.sets.setdefault(key, set()).discard(value)

    async def sismember(self, key, value):
        return value in self.sets.get(key, set())

    async def smembers(self, key):
        return self.sets.get(key, set())


def test_calculate_next_daily_run_uses_today_or_tomorrow():
    timezone = ZoneInfo("Europe/Moscow")

    assert calculate_next_daily_run(
        datetime(2026, 6, 10, 19, 0, tzinfo=timezone),
        "20:00",
    ) == datetime(2026, 6, 10, 20, 0, tzinfo=timezone)
    assert calculate_next_daily_run(
        datetime(2026, 6, 10, 20, 0, tzinfo=timezone),
        "20:00",
    ) == datetime(2026, 6, 11, 20, 0, tzinfo=timezone)


def test_build_daily_digest_schedule_spreads_and_sorts(monkeypatch):
    timezone = ZoneInfo("Europe/Moscow")
    scheduled_for = datetime(2026, 6, 10, 20, 0, tzinfo=timezone)
    offsets = iter([15, -30, 5])

    import sumbot.daily_digest as daily_digest

    monkeypatch.setattr(daily_digest.random, "randint", lambda a, b: next(offsets))

    schedule = build_daily_digest_schedule([1, 2, 3], scheduled_for, 30)

    assert schedule == [
        (2, datetime(2026, 6, 10, 19, 59, 30, tzinfo=timezone)),
        (3, datetime(2026, 6, 10, 20, 0, 5, tzinfo=timezone)),
        (1, datetime(2026, 6, 10, 20, 0, 15, tzinfo=timezone)),
    ]


@pytest.mark.asyncio
async def test_default_digest_uses_active_groups_and_respects_explicit_disable(monkeypatch):
    import sumbot.daily_digest as daily_digest

    redis = FakeRedis()
    known_chats = [
        SimpleNamespace(chat_id=-1001, chat_type="supergroup"),
        SimpleNamespace(chat_id=-1002, chat_type="group"),
        SimpleNamespace(chat_id=7, chat_type="private"),
    ]

    async def fake_fetch_known_bot_chats(db_engine, active_only=True):
        assert db_engine is engine
        assert active_only is True
        return known_chats

    engine = object()
    monkeypatch.setattr(config, "DAILY_DIGEST_DEFAULT_ENABLED", True)
    monkeypatch.setattr(daily_digest, "fetch_known_bot_chats", fake_fetch_known_bot_chats)
    await set_daily_digest_enabled(redis, -1002, False)

    assert await fetch_daily_digest_chat_ids(redis, engine) == [-1001]
    assert await is_daily_digest_enabled(redis, -1001)
    assert not await is_daily_digest_enabled(redis, -1002)
    assert redis.sets[DAILY_DIGEST_DISABLED_CHATS_KEY] == {-1002}


@pytest.mark.asyncio
async def test_digest_on_removes_explicit_disable(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(config, "DAILY_DIGEST_DEFAULT_ENABLED", True)

    await set_daily_digest_enabled(redis, 42, False)
    await set_daily_digest_enabled(redis, 42, True)

    assert await is_daily_digest_enabled(redis, 42)
    assert redis.sets[DAILY_DIGEST_DISABLED_CHATS_KEY] == set()
    assert redis.sets[DAILY_DIGEST_ENABLED_CHATS_KEY] == {42}


@pytest.mark.asyncio
async def test_daily_digest_skips_recent_manual_summary_and_runs_once(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    redis = FakeRedis()
    services = SimpleNamespace(redis=redis)
    await set_daily_digest_enabled(redis, 42, True)
    now = datetime(2026, 6, 10, 20, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    await record_manual_summary_request(redis, 42, requested_at=now.timestamp() - 60)
    monkeypatch.setattr(config, "DAILY_DIGEST_MANUAL_SUPPRESSION_SECONDS", 3_600)

    calls = []

    async def fake_process_summary_command(*args, **kwargs):
        calls.append((args, kwargs))
        return "success"

    monkeypatch.setattr(summary_handlers, "process_summary_command", fake_process_summary_command)

    first = await run_daily_digest_cycle(object(), services, now=now)
    second = await run_daily_digest_cycle(object(), services, now=now)

    assert first == DailyDigestResult(skipped_manual=1)
    assert second == DailyDigestResult(skipped_already_run=1)
    assert calls == []
    assert redis.sets[DAILY_DIGEST_ENABLED_CHATS_KEY] == {42}


@pytest.mark.asyncio
async def test_daily_digest_runs_when_suppression_is_disabled(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    redis = FakeRedis()
    services = SimpleNamespace(redis=redis)
    await set_daily_digest_enabled(redis, 42, True)
    now = datetime(2026, 6, 10, 20, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    await record_manual_summary_request(redis, 42, requested_at=now.timestamp() - 60)
    monkeypatch.setattr(config, "DAILY_DIGEST_MANUAL_SUPPRESSION_SECONDS", 0)

    async def fake_process_summary_command(*args, **kwargs):
        return "success"

    monkeypatch.setattr(summary_handlers, "process_summary_command", fake_process_summary_command)

    assert await run_daily_digest_cycle(object(), services, now=now) == DailyDigestResult(generated=1)


@pytest.mark.asyncio
async def test_debug_user_can_manage_digest_without_group_admin_status():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=0),
        chat=SimpleNamespace(id=-1001),
        bot=SimpleNamespace(),
    )

    assert await can_manage_daily_digest(message)


@pytest.mark.asyncio
async def test_group_admin_can_manage_digest():
    async def fake_get_chat_member(chat_id, user_id):
        assert chat_id == -1001
        assert user_id == 42
        return SimpleNamespace(status="administrator")

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(id=-1001),
        bot=SimpleNamespace(get_chat_member=fake_get_chat_member),
    )

    assert await can_manage_daily_digest(message)
