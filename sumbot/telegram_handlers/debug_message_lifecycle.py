import logging

from aiogram import types

from sumbot.services import BotServices
from sumbot.telegram_handlers.common import delete_after_delay

logger = logging.getLogger("SumBot.telegram_handlers.debug")


def build_debug_command_message_key(chat_id: int, debug_message_id: int) -> str:
    return f"debug_command_message:{chat_id}:{debug_message_id}"


async def delete_debug_message_pair(services: BotServices, debug_message: types.Message) -> None:
    chat_id = debug_message.chat.id
    key = build_debug_command_message_key(chat_id, debug_message.message_id)
    command_message_id = await services.redis.get(key)
    if isinstance(command_message_id, bytes):
        command_message_id = command_message_id.decode()

    if command_message_id:
        try:
            await debug_message.bot.delete_message(chat_id, int(command_message_id))
        except Exception as exc:
            logger.warning("Не удалось удалить исходную команду: %s", exc)

    await services.redis.delete(key)
    await delete_after_delay(debug_message, delay_seconds=0)
