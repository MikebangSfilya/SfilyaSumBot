import asyncio
import logging

from aiogram import types

from sumbot.constants import DEBUG_USER_ID

logger = logging.getLogger("SumBot.telegram_handlers.common")


def is_debug_user(user: types.User | None) -> bool:
    return bool(user and user.id == DEBUG_USER_ID)


async def send_debug_access_denied(message: types.Message) -> None:
    logger.warning(
        "Rejected debug command from unauthorized user (chat_id=%s, user_id=%s)",
        message.chat.id,
        getattr(message.from_user, "id", None),
    )
    not_allowed_message = await message.answer(
        "⛔️ Эта команда недоступна для всех.",
        parse_mode="Markdown",
    )
    await delete_after_delay(not_allowed_message, message, delay_seconds=3)


def schedule_delete_after_delay(*messages: types.Message, delay_seconds: int) -> None:
    asyncio.create_task(delete_after_delay(*messages, delay_seconds=delay_seconds))


def schedule_remove_reply_markup_after_delay(message: types.Message, delay_seconds: int) -> None:
    asyncio.create_task(remove_reply_markup_after_delay(message, delay_seconds=delay_seconds))


async def delete_after_delay(*messages: types.Message, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    for message in messages:
        try:
            await message.delete()
        except Exception as exc:
            logger.warning("Не удалось удалить сообщение (возможно нет прав админа): %s", exc)


async def remove_reply_markup_after_delay(message: types.Message, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Не удалось убрать inline-кнопки у сообщения: %s", exc)
