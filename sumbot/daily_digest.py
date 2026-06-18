import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import config
from sumbot.chat_registry import fetch_known_bot_chats
from sumbot.engagement import was_manual_summary_requested_recently

logger = logging.getLogger("SumBot.daily_digest")

DAILY_DIGEST_ENABLED_CHATS_KEY = "daily_digest:enabled_chats"
DAILY_DIGEST_DISABLED_CHATS_KEY = "daily_digest:disabled_chats"
DAILY_DIGEST_RUN_KEY_PREFIX = "daily_digest:run"


@dataclass(slots=True)
class DailyDigestResult:
    generated: int = 0
    skipped_manual: int = 0
    skipped_already_run: int = 0
    skipped_insufficient: int = 0
    failed: int = 0


class AutomaticSummaryMessage:
    def __init__(self, bot: Any, chat_id: int) -> None:
        self.bot = bot
        self.chat = SimpleNamespace(id=chat_id, type="supergroup")
        self.from_user = SimpleNamespace(id=0)
        self.text = "/summary"

    async def answer(self, text: str, **kwargs: Any) -> Any:
        return await self.bot.send_message(self.chat.id, text, **kwargs)


def build_daily_digest_run_key(chat_id: int, local_date: str) -> str:
    return f"{DAILY_DIGEST_RUN_KEY_PREFIX}:{chat_id}:{local_date}"


async def set_daily_digest_enabled(redis: Any, chat_id: int, enabled: bool) -> None:
    if enabled:
        await redis.sadd(DAILY_DIGEST_ENABLED_CHATS_KEY, chat_id)
        await redis.srem(DAILY_DIGEST_DISABLED_CHATS_KEY, chat_id)
    else:
        await redis.srem(DAILY_DIGEST_ENABLED_CHATS_KEY, chat_id)
        await redis.sadd(DAILY_DIGEST_DISABLED_CHATS_KEY, chat_id)


async def is_daily_digest_enabled(redis: Any, chat_id: int) -> bool:
    if await redis.sismember(DAILY_DIGEST_DISABLED_CHATS_KEY, chat_id):
        return False
    if config.DAILY_DIGEST_DEFAULT_ENABLED:
        return True
    return bool(await redis.sismember(DAILY_DIGEST_ENABLED_CHATS_KEY, chat_id))


async def fetch_daily_digest_chat_ids(redis: Any, db_engine: Any = None) -> list[int]:
    raw_enabled_chat_ids = await redis.smembers(DAILY_DIGEST_ENABLED_CHATS_KEY)
    enabled_chat_ids = {int(chat_id) for chat_id in raw_enabled_chat_ids}

    if config.DAILY_DIGEST_DEFAULT_ENABLED and db_engine is not None:
        known_chats = await fetch_known_bot_chats(db_engine, active_only=True)
        enabled_chat_ids.update(
            chat.chat_id
            for chat in known_chats
            if chat.chat_type in {"group", "supergroup"}
        )

    raw_disabled_chat_ids = await redis.smembers(DAILY_DIGEST_DISABLED_CHATS_KEY)
    disabled_chat_ids = {int(chat_id) for chat_id in raw_disabled_chat_ids}
    return sorted(enabled_chat_ids - disabled_chat_ids)


def parse_daily_digest_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid DAILY_DIGEST_TIME: {value!r}") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid DAILY_DIGEST_TIME: {value!r}")
    return hour, minute


def calculate_next_daily_run(now: datetime, schedule_time: str) -> datetime:
    hour, minute = parse_daily_digest_time(schedule_time)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def build_daily_digest_schedule(
    chat_ids: list[int],
    scheduled_for: datetime,
    jitter_seconds: int,
) -> list[tuple[int, datetime]]:
    if jitter_seconds <= 0:
        return [(chat_id, scheduled_for) for chat_id in chat_ids]

    scheduled_items = [
        (
            chat_id,
            scheduled_for + timedelta(seconds=random.randint(-jitter_seconds, jitter_seconds)),
        )
        for chat_id in chat_ids
    ]
    return sorted(scheduled_items, key=lambda item: item[1])


async def run_daily_digest_cycle(
    bot: Any,
    services: Any,
    *,
    now: datetime | None = None,
    scheduled_for: datetime | None = None,
) -> DailyDigestResult:
    from sumbot.telegram_handlers.summary import process_summary_command

    timezone = ZoneInfo(config.DAILY_DIGEST_TIMEZONE)
    local_now = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    local_date = local_now.date().isoformat()
    result = DailyDigestResult()
    effective_scheduled_for = (
        scheduled_for.astimezone(timezone)
        if scheduled_for is not None
        else local_now
    )
    chat_ids = await fetch_daily_digest_chat_ids(
        services.redis,
        getattr(services, "db_engine", None),
    )

    for chat_id, scheduled_at in build_daily_digest_schedule(
        chat_ids,
        effective_scheduled_for,
        config.DAILY_DIGEST_JITTER_SECONDS,
    ):
        if now is None:
            delay_seconds = (scheduled_at - datetime.now(timezone)).total_seconds()
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

        run_key = build_daily_digest_run_key(chat_id, local_date)
        acquired = await services.redis.set(run_key, "1", nx=True, ex=48 * 3600)
        if not acquired:
            result.skipped_already_run += 1
            continue

        if await was_manual_summary_requested_recently(
            services.redis,
            chat_id,
            current_time=local_now.timestamp(),
        ):
            result.skipped_manual += 1
            logger.info("Daily digest skipped after recent manual summary (chat_id=%s)", chat_id)
            continue

        try:
            outcome = await process_summary_command(
                services,
                AutomaticSummaryMessage(bot, chat_id),
                source="daily_digest",
                bypass_rate_limit=True,
                summary_notice="Ежедневный автоматический дайджест. Отключить: /digest off",
            )
            if outcome == "success":
                result.generated += 1
            elif outcome == "not_enough_messages":
                result.skipped_insufficient += 1
            else:
                result.failed += 1
        except Exception:
            result.failed += 1
            logger.exception("Daily digest failed (chat_id=%s)", chat_id)

    logger.info(
        "Daily digest cycle finished "
        "(generated=%s, skipped_manual=%s, skipped_already_run=%s, skipped_insufficient=%s, failed=%s)",
        result.generated,
        result.skipped_manual,
        result.skipped_already_run,
        result.skipped_insufficient,
        result.failed,
    )
    return result


async def run_daily_digest_scheduler(bot: Any, services: Any) -> None:
    if not config.DAILY_DIGEST_ENABLED:
        logger.info("Daily digest scheduler is disabled.")
        return

    try:
        timezone = ZoneInfo(config.DAILY_DIGEST_TIMEZONE)
        parse_daily_digest_time(config.DAILY_DIGEST_TIME)
    except (ValueError, KeyError):
        logger.exception(
            "Daily digest scheduler has invalid configuration (time=%r, timezone=%r)",
            config.DAILY_DIGEST_TIME,
            config.DAILY_DIGEST_TIMEZONE,
        )
        return

    while True:
        now = datetime.now(timezone)
        next_run = calculate_next_daily_run(now, config.DAILY_DIGEST_TIME)
        wake_at = next_run - timedelta(seconds=config.DAILY_DIGEST_JITTER_SECONDS)
        delay_seconds = max((wake_at - now).total_seconds(), 0)
        logger.info(
            "Next daily digest scheduled for %s (wake_at=%s, jitter=%ss)",
            next_run.isoformat(),
            wake_at.isoformat(),
            config.DAILY_DIGEST_JITTER_SECONDS,
        )
        await asyncio.sleep(delay_seconds)
        await run_daily_digest_cycle(bot, services, scheduled_for=next_run)
