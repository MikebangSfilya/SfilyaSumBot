import logging
from dataclasses import dataclass

from aiogram import Dispatcher, types
from aiogram.filters import Command

import config
from sumbot.chat_registry import KnownBotChat, fetch_known_bot_chats, format_known_bot_chats, save_chat_snapshot
from sumbot.constants import (
    CHAT_REMINDER_COOLDOWN_SECONDS,
    CHAT_REMINDER_EXCLUDED_CHAT_IDS,
)
from sumbot.services import BotServices
from sumbot.telegram_handlers.common import delete_after_delay, is_debug_user, send_debug_access_denied

logger = logging.getLogger("SumBot.telegram_handlers.reminders")

CHAT_UPDATE_REMINDER_TEXT = (
    "Привет. Это SumBot, я всё ещё тут.\n\n"
    "Я немного обновился: стал аккуратнее держать контекст, ответы и упоминания в чате.\n\n"
    "В отдельных чатах можно точечно включить усиленный режим пересказа. "
    "Если хотите попробовать — напишите владельцу бота.\n\n"
    "Если забыли, как пользоваться: /summary делает пересказ свежих сообщений, "
    "а /summary 50 берёт последние 50 сообщений.\n\n"
    "Если бот больше не нужен, меня можно просто удалить из чата."
)

PROMPT_SETTINGS_ANNOUNCEMENT_TEXT = (
    "У SumBot появились настройки пересказа для этого чата.\n\n"
    "Администраторы группы теперь могут вызвать /prompt и отдельно выбрать стиль, тон и уровень "
    "агрессивности. Настройки применяются ко всем следующим пересказам в этом чате.\n\n"
    "Если результат не понравился, в той же панели можно вернуть стандартные настройки. "
    "Обычный пересказ по-прежнему вызывается командой /summary."
)


@dataclass(slots=True)
class ChatReminderResult:
    sent: int
    failed: int
    skipped_cooldown: int = 0
    skipped_excluded: int = 0
    skipped_non_group: int = 0


def register_reminder_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.message(Command("debug_chats"))
    async def debug_chats_cmd(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)

        if not is_debug_user(message.from_user):
            await send_debug_access_denied(message)
            return

        if not services.db_engine:
            logger.warning("Debug chats requested without database connection (chat_id=%s)", message.chat.id)
            await message.answer("База данных не подключена, список чатов недоступен.")
            return

        try:
            chats = await fetch_known_bot_chats(services.db_engine, active_only=True)
            logger.info(
                "Debug chats command executed (chat_id=%s, user_id=%s, active_chats=%s)",
                message.chat.id,
                message.from_user.id,
                len(chats),
            )
            response = format_known_bot_chats(chats)
        except Exception as exc:
            logger.error("Debug chats error: %s", exc, exc_info=True)
            await message.answer("Не удалось получить список чатов из базы.")
            return

        debug_message = await message.answer(
            response,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await delete_after_delay(
            debug_message,
            message,
            delay_seconds=config.ChatConfig.SHORT_DELAY_SEC,
        )

    @dispatcher.message(Command("debug_remind_chats"))
    async def debug_remind_chats_cmd(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)

        if not is_debug_user(message.from_user):
            await send_debug_access_denied(message)
            return

        if not services.db_engine:
            logger.warning("Debug remind chats requested without database connection (chat_id=%s)", message.chat.id)
            await message.answer("База данных не подключена, рассылка по чатам недоступна.")
            return

        try:
            chats = await fetch_known_bot_chats(services.db_engine, active_only=True)
            force = any(arg.lower() in {"force", "--force"} for arg in (message.text or "").split()[1:])
            result = await send_chat_update_reminders(
                message.bot,
                chats,
                redis=services.redis,
                force=force,
            )
            logger.info(
                "Debug remind chats command executed "
                "(chat_id=%s, user_id=%s, active_chats=%s, sent=%s, failed=%s, "
                "skipped_cooldown=%s, skipped_excluded=%s, force=%s)",
                message.chat.id,
                message.from_user.id,
                len(chats),
                result.sent,
                result.failed,
                result.skipped_cooldown,
                result.skipped_excluded,
                force,
            )
        except Exception as exc:
            logger.error("Debug remind chats error: %s", exc, exc_info=True)
            await message.answer("Не удалось отправить напоминание по чатам.")
            return

        await message.answer(
            "Готово.\n"
            f"Чатов в базе: {len(chats)}\n"
            f"Отправлено: {result.sent}\n"
            f"Пропущено по таймеру: {result.skipped_cooldown}\n"
            f"Пропущено по exclude-list: {result.skipped_excluded}\n"
            f"Ошибок: {result.failed}"
        )

    @dispatcher.message(Command("debug_announce_prompt"))
    async def debug_announce_prompt_cmd(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)

        if not is_debug_user(message.from_user):
            await send_debug_access_denied(message)
            return
        if not services.db_engine:
            await message.answer("База данных не подключена, рассылка по группам недоступна.")
            return

        try:
            chats = await fetch_known_bot_chats(services.db_engine, active_only=True)
            force = any(arg.lower() in {"force", "--force"} for arg in (message.text or "").split()[1:])
            result = await send_prompt_settings_announcements(
                message.bot,
                chats,
                redis=services.redis,
                force=force,
            )
            logger.info(
                "Prompt settings announcement executed "
                "(chat_id=%s, user_id=%s, active_chats=%s, sent=%s, failed=%s, "
                "skipped_cooldown=%s, skipped_excluded=%s, skipped_non_group=%s, force=%s)",
                message.chat.id,
                message.from_user.id,
                len(chats),
                result.sent,
                result.failed,
                result.skipped_cooldown,
                result.skipped_excluded,
                result.skipped_non_group,
                force,
            )
        except Exception as exc:
            logger.error("Prompt settings announcement error: %s", exc, exc_info=True)
            await message.answer("Не удалось отправить анонс по группам.")
            return

        await message.answer(
            "Готово.\n"
            f"Чатов в базе: {len(chats)}\n"
            f"Отправлено в группы: {result.sent}\n"
            f"Пропущено по таймеру: {result.skipped_cooldown}\n"
            f"Пропущено по exclude-list: {result.skipped_excluded}\n"
            f"Пропущено не-групп: {result.skipped_non_group}\n"
            f"Ошибок: {result.failed}"
        )


def build_chat_update_reminder_key(chat_id: int) -> str:
    return f"chat_update_reminder:{chat_id}"


def build_prompt_settings_announcement_key(chat_id: int) -> str:
    return f"prompt_settings_announcement:{chat_id}"


async def send_chat_update_reminders(
    bot: object,
    chats: list[KnownBotChat],
    *,
    redis: object | None = None,
    cooldown_seconds: int = CHAT_REMINDER_COOLDOWN_SECONDS,
    excluded_chat_ids: frozenset[int] = CHAT_REMINDER_EXCLUDED_CHAT_IDS,
    force: bool = False,
) -> ChatReminderResult:
    sent = 0
    failed = 0
    skipped_cooldown = 0
    skipped_excluded = 0
    for chat in chats:
        if chat.chat_id in excluded_chat_ids:
            skipped_excluded += 1
            logger.info("Skipped chat update reminder for excluded chat (chat_id=%s)", chat.chat_id)
            continue

        reminder_key = build_chat_update_reminder_key(chat.chat_id)
        cooldown_acquired = True
        if redis and cooldown_seconds > 0 and not force:
            cooldown_acquired = bool(await redis.set(reminder_key, "1", nx=True, ex=cooldown_seconds))

        if not cooldown_acquired:
            skipped_cooldown += 1
            logger.info("Skipped chat update reminder due to cooldown (chat_id=%s)", chat.chat_id)
            continue

        try:
            await bot.send_message(chat.chat_id, CHAT_UPDATE_REMINDER_TEXT)
            sent += 1
        except Exception as exc:
            failed += 1
            if redis and cooldown_seconds > 0 and not force:
                await redis.delete(reminder_key)
            logger.warning(
                "Failed to send chat update reminder (chat_id=%s, error_type=%s)",
                chat.chat_id,
                type(exc).__name__,
                exc_info=True,
            )
    return ChatReminderResult(
        sent=sent,
        failed=failed,
        skipped_cooldown=skipped_cooldown,
        skipped_excluded=skipped_excluded,
    )


async def send_prompt_settings_announcements(
    bot: object,
    chats: list[KnownBotChat],
    *,
    redis: object | None = None,
    cooldown_seconds: int = CHAT_REMINDER_COOLDOWN_SECONDS,
    excluded_chat_ids: frozenset[int] = CHAT_REMINDER_EXCLUDED_CHAT_IDS,
    force: bool = False,
) -> ChatReminderResult:
    sent = 0
    failed = 0
    skipped_cooldown = 0
    skipped_excluded = 0
    skipped_non_group = 0
    for chat in chats:
        if chat.chat_id in excluded_chat_ids:
            skipped_excluded += 1
            continue
        if chat.chat_type not in {"group", "supergroup"}:
            skipped_non_group += 1
            continue

        announcement_key = build_prompt_settings_announcement_key(chat.chat_id)
        cooldown_acquired = True
        if redis and cooldown_seconds > 0 and not force:
            cooldown_acquired = bool(await redis.set(announcement_key, "1", nx=True, ex=cooldown_seconds))
        if not cooldown_acquired:
            skipped_cooldown += 1
            continue

        try:
            await bot.send_message(chat.chat_id, PROMPT_SETTINGS_ANNOUNCEMENT_TEXT)
            sent += 1
        except Exception as exc:
            failed += 1
            if redis and cooldown_seconds > 0 and not force:
                await redis.delete(announcement_key)
            logger.warning(
                "Failed to send prompt settings announcement (chat_id=%s, error_type=%s)",
                chat.chat_id,
                type(exc).__name__,
                exc_info=True,
            )

    return ChatReminderResult(
        sent=sent,
        failed=failed,
        skipped_cooldown=skipped_cooldown,
        skipped_excluded=skipped_excluded,
        skipped_non_group=skipped_non_group,
    )
