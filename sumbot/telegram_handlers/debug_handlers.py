import logging
import time
from aiogram import Dispatcher, F, types
from aiogram.filters import Command

from sumbot.chat_registry import save_chat_snapshot
from sumbot.chunks import get_chunk_runtime_stats
from sumbot.constants import DEFAULT_SUMMARY_PERIOD_SECONDS, DEBUG_USER_ID
from sumbot.history import build_chat_history_key
from sumbot.services import BotServices
from sumbot.telegram_handlers.common import (
    is_debug_user,
    schedule_delete_after_delay,
    send_debug_access_denied,
)
from sumbot.telegram_handlers.debug_constants import (
    CHUNKING_SETTINGS_CALLBACK_PREFIX,
    DEBUG_DELETE_CALLBACK_DATA,
    DEBUG_MESSAGE_AUTO_DELETE_SECONDS,
    LLM_MODEL_SETTINGS_CALLBACK_PREFIX,
    SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX,
    SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX,
)
from sumbot.telegram_handlers.debug_panel import build_debug_info, build_llm_model_settings_keyboard
from sumbot.telegram_handlers.debug_message_lifecycle import (
    build_debug_command_message_key,
    delete_debug_message_pair,
)
from sumbot.telegram_handlers.debug_runtime import refresh_debug_settings_message
from sumbot.telegram_handlers.debug_stats import count_recent_logs
from sumbot.telegram_handlers.settings_panel import (
    format_summary_thinking_mode,
    format_summary_tokens_setting,
    parse_summary_tokens_callback_value,
)

logger = logging.getLogger("SumBot.telegram_handlers.debug")


def register_debug_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.message(Command("debug"))
    async def debug_cmd(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)

        if not is_debug_user(message.from_user):
            await send_debug_access_denied(message)
            return

        chat_id = message.chat.id
        raw_logs = await services.redis.lrange(build_chat_history_key(chat_id), 0, -1)
        current_ts = time.time()
        last_day_count = count_recent_logs(raw_logs, current_ts, period_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS)
        db_status = "✅ Подключена" if services.db_engine else "❌ Нет связи"
        active_model = await services.get_active_llm_model(chat_id)
        generation_settings = await services.get_summary_generation_settings(chat_id)
        presentation_settings = await services.get_summary_presentation_settings(chat_id)
        chunking_enabled = await services.is_chunking_enabled(chat_id)
        chunk_stats = await get_chunk_runtime_stats(services.redis, chat_id)
        logger.info(
            "Debug command executed "
            "(chat_id=%s, user_id=%s, redis_logs=%s, logs_24h=%s, db_enabled=%s, "
            "model_id=%s, provider=%s, model=%s, max_output_tokens=%s, thinking_mode=%s, "
            "style=%s, tone=%s, aggressiveness=%s, chunking_enabled=%s, "
            "active_chunk_size=%s, summarized_chunk_count=%s, chunk_status=%s)",
            chat_id,
            message.from_user.id,
            len(raw_logs),
            last_day_count,
            services.db_engine is not None,
            active_model.option.model_id,
            active_model.option.provider,
            active_model.option.model_name,
            generation_settings.max_output_tokens,
            generation_settings.thinking_mode,
            presentation_settings.style.option_id,
            presentation_settings.tone.option_id,
            presentation_settings.aggressiveness.level,
            chunking_enabled,
            chunk_stats.active_chunk_size,
            chunk_stats.summarized_chunk_count,
            chunk_stats.last_status,
        )

        debug_message = await message.answer(
            build_debug_info(
                len(raw_logs),
                last_day_count,
                db_status,
                active_model,
                generation_settings,
                presentation_settings,
                chunking_enabled=chunking_enabled,
                chunk_stats=chunk_stats,
            ),
            parse_mode="HTML",
            reply_markup=build_llm_model_settings_keyboard(
                services,
                active_model.option.model_id,
                generation_settings,
                presentation_settings,
                chunking_enabled=chunking_enabled,
            ),
        )
        await services.redis.set(
            build_debug_command_message_key(chat_id, debug_message.message_id),
            str(message.message_id),
            ex=DEBUG_MESSAGE_AUTO_DELETE_SECONDS,
        )
        schedule_delete_after_delay(
            debug_message,
            message,
            delay_seconds=DEBUG_MESSAGE_AUTO_DELETE_SECONDS,
        )

    @dispatcher.callback_query(F.data == DEBUG_DELETE_CALLBACK_DATA)
    async def debug_delete_callback(callback: types.CallbackQuery) -> None:
        message = callback.message
        chat = getattr(message, "chat", None)
        if chat is not None:
            await save_chat_snapshot(services.db_engine, chat)

        if callback.from_user.id != DEBUG_USER_ID:
            logger.warning(
                "Rejected debug delete callback from unauthorized user "
                "(chat_id=%s, user_id=%s)",
                getattr(chat, "id", None),
                callback.from_user.id,
            )
            await callback.answer("Эта кнопка недоступна.", show_alert=True)
            return

        if message is None or chat is None:
            await callback.answer("Сообщение уже недоступно.", show_alert=True)
            return

        await callback.answer("Удаляю debug-сообщение.")
        await delete_debug_message_pair(services, message)

    @dispatcher.callback_query(F.data.startswith(LLM_MODEL_SETTINGS_CALLBACK_PREFIX))
    async def llm_model_settings_callback(callback: types.CallbackQuery) -> None:
        message = callback.message
        chat = getattr(message, "chat", None)
        if chat is not None:
            await save_chat_snapshot(services.db_engine, chat)

        if callback.from_user.id != DEBUG_USER_ID:
            logger.warning(
                "Rejected LLM model settings callback from unauthorized user "
                "(chat_id=%s, user_id=%s, callback_data=%r)",
                getattr(chat, "id", None),
                callback.from_user.id,
                callback.data,
            )
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        if chat is None:
            await callback.answer("Чат уже недоступен.", show_alert=True)
            return

        model_id = (callback.data or "").removeprefix(LLM_MODEL_SETTINGS_CALLBACK_PREFIX)
        try:
            active_model = await services.set_active_llm_model(chat.id, model_id)
        except KeyError:
            logger.warning(
                "Unknown LLM model settings callback (chat_id=%s, user_id=%s, model_id=%r)",
                getattr(chat, "id", None),
                callback.from_user.id,
                model_id,
            )
            await callback.answer("Неизвестная модель.", show_alert=True)
            return
        except ValueError:
            option = services.model_options.get(model_id)
            provider = option.provider if option else "этого провайдера"
            logger.warning(
                "Unavailable LLM model selected (chat_id=%s, user_id=%s, model_id=%r)",
                getattr(chat, "id", None),
                callback.from_user.id,
                model_id,
            )
            await callback.answer(f"Для {provider} не задан API key.", show_alert=True)
            return

        if message is not None and hasattr(message, "edit_text"):
            generation_settings = await services.get_summary_generation_settings(chat.id)
            await refresh_debug_settings_message(services, message, active_model, generation_settings)

        await callback.answer(f"Выбрано: {active_model.option.label}")

    @dispatcher.callback_query(F.data.startswith(SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX))
    async def summary_tokens_settings_callback(callback: types.CallbackQuery) -> None:
        message = callback.message
        chat = getattr(message, "chat", None)
        if chat is not None:
            await save_chat_snapshot(services.db_engine, chat)

        if callback.from_user.id != DEBUG_USER_ID:
            logger.warning(
                "Rejected summary tokens settings callback from unauthorized user "
                "(chat_id=%s, user_id=%s, callback_data=%r)",
                getattr(chat, "id", None),
                callback.from_user.id,
                callback.data,
            )
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        if chat is None:
            await callback.answer("Чат уже недоступен.", show_alert=True)
            return

        token_value = (callback.data or "").removeprefix(SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX)
        try:
            max_output_tokens = parse_summary_tokens_callback_value(token_value)
            generation_settings = await services.set_summary_max_output_tokens(chat.id, max_output_tokens)
        except ValueError:
            logger.warning(
                "Invalid summary tokens settings callback (chat_id=%s, user_id=%s, value=%r)",
                chat.id,
                callback.from_user.id,
                token_value,
            )
            await callback.answer("Неизвестный лимит токенов.", show_alert=True)
            return

        active_model = await services.get_active_llm_model(chat.id)
        if message is not None and hasattr(message, "edit_text"):
            await refresh_debug_settings_message(services, message, active_model, generation_settings)

        await callback.answer(f"Output tokens: {format_summary_tokens_setting(generation_settings)}")

    @dispatcher.callback_query(F.data.startswith(SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX))
    async def summary_thinking_settings_callback(callback: types.CallbackQuery) -> None:
        message = callback.message
        chat = getattr(message, "chat", None)
        if chat is not None:
            await save_chat_snapshot(services.db_engine, chat)

        if callback.from_user.id != DEBUG_USER_ID:
            logger.warning(
                "Rejected summary thinking settings callback from unauthorized user "
                "(chat_id=%s, user_id=%s, callback_data=%r)",
                getattr(chat, "id", None),
                callback.from_user.id,
                callback.data,
            )
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        if chat is None:
            await callback.answer("Чат уже недоступен.", show_alert=True)
            return

        thinking_mode = (callback.data or "").removeprefix(SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX)
        try:
            generation_settings = await services.set_summary_thinking_mode(chat.id, thinking_mode)
        except ValueError:
            logger.warning(
                "Invalid summary thinking settings callback (chat_id=%s, user_id=%s, value=%r)",
                chat.id,
                callback.from_user.id,
                thinking_mode,
            )
            await callback.answer("Неизвестный режим thinking.", show_alert=True)
            return

        active_model = await services.get_active_llm_model(chat.id)
        if message is not None and hasattr(message, "edit_text"):
            await refresh_debug_settings_message(services, message, active_model, generation_settings)

        await callback.answer(f"Thinking: {format_summary_thinking_mode(generation_settings)}")

    @dispatcher.callback_query(F.data.startswith(CHUNKING_SETTINGS_CALLBACK_PREFIX))
    async def chunking_settings_callback(callback: types.CallbackQuery) -> None:
        message = callback.message
        chat = getattr(message, "chat", None)
        if chat is not None:
            await save_chat_snapshot(services.db_engine, chat)

        if callback.from_user.id != DEBUG_USER_ID:
            logger.warning(
                "Rejected chunking settings callback from unauthorized user "
                "(chat_id=%s, user_id=%s, callback_data=%r)",
                getattr(chat, "id", None),
                callback.from_user.id,
                callback.data,
            )
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        if chat is None:
            await callback.answer("Чат уже недоступен.", show_alert=True)
            return

        enabled_value = (callback.data or "").removeprefix(CHUNKING_SETTINGS_CALLBACK_PREFIX)
        if enabled_value not in {"on", "off"}:
            await callback.answer("Неизвестный режим chunking.", show_alert=True)
            return

        enabled = await services.set_chunking_enabled(chat.id, enabled_value == "on")
        active_model = await services.get_active_llm_model(chat.id)
        generation_settings = await services.get_summary_generation_settings(chat.id)
        if message is not None and hasattr(message, "edit_text"):
            await refresh_debug_settings_message(services, message, active_model, generation_settings)

        await callback.answer(f"Chunking: {'включен' if enabled else 'выключен'}")
