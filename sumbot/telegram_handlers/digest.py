import logging

from aiogram import Dispatcher, types
from aiogram.filters import Command

import config
from sumbot.chat_registry import normalize_chat_type
from sumbot.daily_digest import is_daily_digest_enabled, set_daily_digest_enabled
from sumbot.services import BotServices
from sumbot.telegram_handlers.common import is_debug_user

logger = logging.getLogger("SumBot.telegram_handlers.digest")

ADMIN_STATUSES = {"administrator", "creator"}


def register_digest_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.message(Command("digest"))
    async def digest_cmd(message: types.Message) -> None:
        if not message.from_user or normalize_chat_type(message.chat.type) not in {"group", "supergroup"}:
            await message.answer("Ежедневный дайджест можно настроить только в группе.")
            return
        if not await can_manage_daily_digest(message):
            await message.answer("Настраивать ежедневный дайджест могут только администраторы группы.")
            return

        args = (message.text or "").split()
        action = args[1].lower() if len(args) > 1 else "status"
        if action in {"on", "enable"}:
            await set_daily_digest_enabled(services.redis, message.chat.id, True)
            await message.answer(
                f"Ежедневный дайджест включен на {config.DAILY_DIGEST_TIME} "
                f"({config.DAILY_DIGEST_TIMEZONE}). Если /summary вызвали незадолго до запуска, "
                "автоматический пересказ будет пропущен."
            )
            return
        if action in {"off", "disable"}:
            await set_daily_digest_enabled(services.redis, message.chat.id, False)
            await message.answer("Ежедневный дайджест отключен.")
            return
        if action != "status":
            await message.answer("Использование: /digest on, /digest off или /digest status.")
            return

        enabled = await is_daily_digest_enabled(services.redis, message.chat.id)
        state = "включен" if enabled else "выключен"
        mode = "по умолчанию для всех групп" if config.DAILY_DIGEST_DEFAULT_ENABLED else "только после /digest on"
        await message.answer(
            f"Ежедневный дайджест {state}. Время: {config.DAILY_DIGEST_TIME} "
            f"({config.DAILY_DIGEST_TIMEZONE}); режим: {mode}."
        )


async def is_chat_admin(message: types.Message) -> bool:
    try:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    except Exception:
        logger.exception(
            "Failed to check digest command permissions (chat_id=%s, user_id=%s)",
            message.chat.id,
            message.from_user.id,
        )
        return False
    status = getattr(member.status, "value", member.status)
    return str(status) in ADMIN_STATUSES


async def can_manage_daily_digest(message: types.Message) -> bool:
    if is_debug_user(message.from_user):
        return True
    return await is_chat_admin(message)
