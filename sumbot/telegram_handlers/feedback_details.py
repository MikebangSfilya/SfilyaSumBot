import asyncio
import logging

from aiogram import Dispatcher, F, types

from sumbot.chat_registry import save_chat_snapshot
from sumbot.chunks import append_message_to_active_chunk
from sumbot.constants import (
    FEEDBACK_DETAILS_DELETE_DELAY_SECONDS,
    FEEDBACK_DETAILS_PROMPT_TIMEOUT_SECONDS,
)
from sumbot.feedback import (
    SUMMARY_FEEDBACK_CALLBACK_PREFIX,
    SUMMARY_FEEDBACK_DETAILS_CALLBACK_PREFIX,
    acquire_summary_feedback_rate_limit,
    clear_pending_feedback_details,
    get_pending_feedback_details,
    has_feedback_for_summary,
    normalize_feedback_details,
    parse_summary_feedback_callback,
    save_feedback_details_for_summary,
    save_feedback_for_summary,
    save_pending_feedback_details,
)
from sumbot.history import save_message_to_history
from sumbot.engagement import maybe_send_onboarding_ready_hint
from sumbot.metrics import inc_telegram_updates, observe_feedback
from sumbot.services import BotServices
from sumbot.telegram_handlers.common import delete_after_delay, schedule_delete_after_delay

logger = logging.getLogger("SumBot.telegram_handlers.feedback_details")

FEEDBACK_DETAILS_PROMPT_SUFFIX = "что добавить к оценке? Ответь на это сообщение одним коротким комментарием."
FEEDBACK_DETAILS_PLACEHOLDER = "Например: слишком длинно, не понял контекст, стало лучше, выдумал факты"


def register_feedback_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.callback_query(F.data.startswith(SUMMARY_FEEDBACK_CALLBACK_PREFIX))
    async def summary_feedback_callback(callback: types.CallbackQuery) -> None:
        await handle_summary_feedback_callback(services, callback)

    @dispatcher.callback_query(F.data.startswith(SUMMARY_FEEDBACK_DETAILS_CALLBACK_PREFIX))
    async def summary_feedback_details_callback(callback: types.CallbackQuery) -> None:
        await handle_summary_feedback_details_callback(services, callback)

    @dispatcher.message()
    async def catch_all(message: types.Message) -> None:
        inc_telegram_updates()
        await save_chat_snapshot(services.db_engine, message.chat)
        if await handle_pending_feedback_details(services, message):
            return
        history_payload = await save_message_to_history(services.redis, message)
        if history_payload:
            await maybe_send_onboarding_ready_hint(services.redis, message)
        if history_payload and await services.is_chunking_enabled(message.chat.id):
            await append_message_to_active_chunk(services.redis, message.chat.id, history_payload)


async def handle_summary_feedback_callback(services: BotServices, callback: types.CallbackQuery) -> None:
    parsed_feedback = parse_summary_feedback_callback(callback.data)
    message = callback.message
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", None)
    if chat is not None:
        await save_chat_snapshot(services.db_engine, chat)

    if not parsed_feedback or chat_id is None or message_id is None:
        logger.warning(
            "Invalid summary feedback callback "
            "(callback_data=%r, chat_id=%s, message_id=%s, user_id=%s)",
            callback.data,
            chat_id,
            message_id,
            callback.from_user.id,
        )
        await callback.answer("Не удалось сохранить фидбек.")
        return

    feedback_value, sentiment = parsed_feedback
    rate_limit_acquired = await acquire_summary_feedback_rate_limit(
        services.redis,
        chat_id,
        message_id,
        callback.from_user.id,
        "rating",
    )
    if not rate_limit_acquired:
        logger.info(
            "Summary feedback callback rate limited "
            "(chat_id=%s, message_id=%s, user_id=%s, value=%s)",
            chat_id,
            message_id,
            callback.from_user.id,
            feedback_value,
        )
        await callback.answer("Фидбек уже обрабатывается, подожди пару секунд.")
        return

    observe_feedback(feedback_value)
    logger.info(
        "Summary feedback received "
        "(chat_id=%s, message_id=%s, user_id=%s, value=%s, sentiment=%s)",
        chat_id,
        message_id,
        callback.from_user.id,
        feedback_value,
        sentiment,
    )
    saved = await save_feedback_for_summary(
        services.db_engine,
        chat_id,
        message_id,
        callback.from_user.id,
        feedback_value,
        sentiment,
    )
    if saved:
        logger.info(
            "Summary feedback callback handled (chat_id=%s, message_id=%s, user_id=%s, saved=True)",
            chat_id,
            message_id,
            callback.from_user.id,
        )
        await callback.answer("Фидбек сохранен. Комментарий можно добавить кнопкой ниже.")
    else:
        logger.warning(
            "Summary feedback callback handled (chat_id=%s, message_id=%s, user_id=%s, saved=False)",
            chat_id,
            message_id,
            callback.from_user.id,
        )
        await callback.answer("Не удалось сохранить фидбек.")


async def handle_summary_feedback_details_callback(
    services: BotServices,
    callback: types.CallbackQuery,
) -> None:
    message = callback.message
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", None)
    if chat is not None:
        await save_chat_snapshot(services.db_engine, chat)

    if chat_id is None or message_id is None:
        logger.warning(
            "Invalid summary feedback details callback "
            "(callback_data=%r, chat_id=%s, message_id=%s, user_id=%s)",
            callback.data,
            chat_id,
            message_id,
            callback.from_user.id,
        )
        await callback.answer("Не удалось запросить комментарий.")
        return

    has_feedback = await has_feedback_for_summary(
        services.db_engine,
        chat_id,
        message_id,
        callback.from_user.id,
    )
    if not has_feedback:
        await callback.answer("Сначала поставь оценку, потом можно добавить комментарий.")
        return

    rate_limit_acquired = await acquire_summary_feedback_rate_limit(
        services.redis,
        chat_id,
        message_id,
        callback.from_user.id,
        "details",
    )
    if not rate_limit_acquired:
        logger.info(
            "Summary feedback details callback rate limited "
            "(chat_id=%s, message_id=%s, user_id=%s)",
            chat_id,
            message_id,
            callback.from_user.id,
        )
        await callback.answer("Комментарий уже запрошен, подожди пару секунд.")
        return

    if not hasattr(message, "answer"):
        await callback.answer("Не удалось запросить комментарий.")
        return

    prompt_message = await message.answer(**build_feedback_details_prompt(callback.from_user))
    await save_pending_feedback_details(
        services.redis,
        chat_id,
        message_id,
        callback.from_user.id,
        prompt_message.message_id,
    )
    schedule_delete_feedback_prompt_on_timeout(
        services,
        chat_id,
        callback.from_user.id,
        prompt_message,
    )
    await callback.answer("Ответь на сообщение бота коротким комментарием.")


def build_feedback_details_prompt(user: types.User) -> dict:
    target_name = user.first_name or "ты"
    text = f"{target_name}, {FEEDBACK_DETAILS_PROMPT_SUFFIX}"
    return {
        "text": text,
        "entities": [
            types.MessageEntity(
                type="text_mention",
                offset=0,
                length=len(target_name),
                user=user,
            )
        ],
        "reply_markup": types.ForceReply(
            selective=True,
            input_field_placeholder=FEEDBACK_DETAILS_PLACEHOLDER,
        ),
    }


async def handle_pending_feedback_details(services: BotServices, message: types.Message) -> bool:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return False

    pending = await get_pending_feedback_details(
        services.redis,
        message.chat.id,
        message.from_user.id,
    )
    if not pending:
        return False

    reply_to_message_id = getattr(getattr(message, "reply_to_message", None), "message_id", None)
    if reply_to_message_id != pending.prompt_message_id:
        return False

    details = normalize_feedback_details(message.text)
    if not details:
        return False

    saved = await save_feedback_details_for_summary(
        services.db_engine,
        pending.chat_id,
        pending.telegram_message_id,
        pending.user_id,
        details,
    )
    if saved:
        await clear_pending_feedback_details(services.redis, message.chat.id, message.from_user.id)
        confirmation_message = await message.answer("Спасибо, записал подробности.")
        prompt_message = getattr(message, "reply_to_message", None)
        messages_to_delete = [message, confirmation_message]
        if prompt_message is not None:
            messages_to_delete.append(prompt_message)
        schedule_delete_after_delay(
            *messages_to_delete,
            delay_seconds=FEEDBACK_DETAILS_DELETE_DELAY_SECONDS,
        )
    else:
        error_message = await message.answer("Не удалось сохранить подробности фидбека.")
        schedule_delete_after_delay(
            error_message,
            delay_seconds=FEEDBACK_DETAILS_DELETE_DELAY_SECONDS,
        )

    return True


def schedule_delete_feedback_prompt_on_timeout(
    services: BotServices,
    chat_id: int,
    user_id: int,
    prompt_message: types.Message,
) -> None:
    asyncio.create_task(
        delete_feedback_prompt_on_timeout(
            services,
            chat_id,
            user_id,
            prompt_message,
            delay_seconds=FEEDBACK_DETAILS_PROMPT_TIMEOUT_SECONDS,
        )
    )


async def delete_feedback_prompt_on_timeout(
    services: BotServices,
    chat_id: int,
    user_id: int,
    prompt_message: types.Message,
    delay_seconds: int,
) -> None:
    await asyncio.sleep(delay_seconds)
    pending = await get_pending_feedback_details(services.redis, chat_id, user_id)
    if not pending or pending.prompt_message_id != prompt_message.message_id:
        return

    await clear_pending_feedback_details(services.redis, chat_id, user_id)
    await delete_after_delay(prompt_message, delay_seconds=0)
