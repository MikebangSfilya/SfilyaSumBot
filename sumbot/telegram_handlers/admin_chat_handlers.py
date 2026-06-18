import logging

from aiogram import Dispatcher, F, types
from aiogram.filters import Command

from sumbot.chat_registry import fetch_known_bot_chats, save_chat_snapshot
from sumbot.chunks import get_chunk_runtime_stats
from sumbot.constants import DEBUG_USER_ID
from sumbot.services import BotServices
from sumbot.telegram_handlers.admin_chat_panel import (
    ADMIN_CHAT_PRESENTATION_BACK_CALLBACK_PREFIX,
    ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX,
    ADMIN_CHAT_PRESENTATION_RESET_CALLBACK_PREFIX,
    ADMIN_CHAT_PRESENTATION_SET_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA,
    ADMIN_CHAT_SETTINGS_COMMAND,
    ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX,
    build_admin_presentation_option_keyboard,
    build_admin_presentation_option_text,
    build_admin_chat_settings_info,
    build_admin_chat_settings_keyboard,
    build_admin_chat_leave_confirmation_keyboard,
    build_admin_chat_leave_confirmation_text,
    build_admin_chat_readd_allowed_text,
    build_admin_chat_back_keyboard,
    build_admin_chat_removed_keyboard,
    build_admin_chat_removed_text,
    build_admin_chat_settings_list_keyboard,
    build_admin_chat_settings_list_text,
    parse_admin_chat_setting_callback,
    parse_admin_chat_settings_target,
    parse_admin_presentation_set_callback,
)
from sumbot.telegram_handlers.admin_chat_runtime import (
    allow_admin_managed_chat_readd,
    edit_admin_chat_settings_message,
    find_known_chat_by_id,
    leave_admin_managed_chat,
    refresh_admin_chat_settings_message,
)
from sumbot.telegram_handlers.common import is_debug_user, send_debug_access_denied
from sumbot.telegram_handlers.settings_panel import (
    format_summary_thinking_mode,
    format_summary_tokens_setting,
    parse_summary_tokens_callback_value,
)
from sumbot.telegram_handlers.prompt_profile_panel import (
    PRESENTATION_COMPONENT_AGGRESSIVENESS,
    PRESENTATION_COMPONENT_STYLE,
    PRESENTATION_COMPONENT_TONE,
    PRESENTATION_COMPONENTS,
)

logger = logging.getLogger("SumBot.telegram_handlers.debug")


def register_admin_chat_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.message(Command(ADMIN_CHAT_SETTINGS_COMMAND))
    async def admin_chat_settings_cmd(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)

        if not is_debug_user(message.from_user):
            await send_debug_access_denied(message)
            return

        try:
            target_chat_id = parse_admin_chat_settings_target(message.text or "")
        except ValueError:
            await message.answer(f"Неверный chat_id. Формат: /{ADMIN_CHAT_SETTINGS_COMMAND} <chat_id>")
            return
        if target_chat_id is not None:
            active_model = await services.get_active_llm_model(target_chat_id)
            generation_settings = await services.get_summary_generation_settings(target_chat_id)
            presentation_settings = await services.get_summary_presentation_settings(target_chat_id)
            chunking_enabled = await services.is_chunking_enabled(target_chat_id)
            chunking_enabled_chats = await services.count_chunking_enabled_chats()
            chunk_stats = await get_chunk_runtime_stats(services.redis, target_chat_id)
            known_chat = await find_known_chat_by_id(services.db_engine, target_chat_id)
            chat_approval_status = await services.get_chat_approval_status(target_chat_id)
            await message.answer(
                build_admin_chat_settings_info(
                    target_chat_id,
                    active_model,
                    generation_settings,
                    presentation_settings,
                    known_chat=known_chat,
                    chat_approval_status=chat_approval_status,
                    chunking_enabled=chunking_enabled,
                    chunking_enabled_chats=chunking_enabled_chats,
                    chunk_stats=chunk_stats,
                ),
                parse_mode="HTML",
                reply_markup=build_admin_chat_settings_keyboard(
                    services,
                    target_chat_id,
                    active_model.option.model_id,
                    generation_settings,
                    presentation_settings,
                    known_chat=known_chat,
                    chat_approval_status=chat_approval_status,
                    chunking_enabled=chunking_enabled,
                ),
                disable_web_page_preview=True,
            )
            return

        if not services.db_engine:
            logger.warning("Admin chat settings requested without database connection (chat_id=%s)", message.chat.id)
            await message.answer(
                "База данных не подключена, список чатов недоступен.\n"
                f"Можно открыть настройки вручную: /{ADMIN_CHAT_SETTINGS_COMMAND} <chat_id>"
            )
            return

        try:
            chats = await fetch_known_bot_chats(services.db_engine, active_only=False)
            chunking_enabled_chats = await services.count_chunking_enabled_chats()
        except Exception as exc:
            logger.error("Admin chat settings list error: %s", exc, exc_info=True)
            await message.answer("Не удалось получить список чатов из базы.")
            return
        await message.answer(
            build_admin_chat_settings_list_text(chats, page=0, chunking_enabled_chats=chunking_enabled_chats),
            parse_mode="HTML",
            reply_markup=build_admin_chat_settings_list_keyboard(chats, page=0),
            disable_web_page_preview=True,
        )

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX))
    async def admin_chat_settings_page_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return
        if not services.db_engine:
            await callback.answer("База данных не подключена.", show_alert=True)
            return
        page_value = (callback.data or "").removeprefix(ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX)
        try:
            page = max(0, int(page_value))
        except ValueError:
            await callback.answer("Неизвестная страница.", show_alert=True)
            return

        try:
            chats = await fetch_known_bot_chats(services.db_engine, active_only=False)
            chunking_enabled_chats = await services.count_chunking_enabled_chats()
        except Exception as exc:
            logger.error("Admin chat settings page error: %s", exc, exc_info=True)
            await callback.answer("Не удалось получить список чатов.", show_alert=True)
            return
        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(
                build_admin_chat_settings_list_text(chats, page=page, chunking_enabled_chats=chunking_enabled_chats),
                parse_mode="HTML",
                reply_markup=build_admin_chat_settings_list_keyboard(chats, page=page),
                disable_web_page_preview=True,
            )
        await callback.answer()

    @dispatcher.callback_query(F.data == ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA)
    async def admin_chat_settings_back_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return
        if not services.db_engine:
            await callback.answer("База данных не подключена.", show_alert=True)
            return
        try:
            chats = await fetch_known_bot_chats(services.db_engine, active_only=False)
            chunking_enabled_chats = await services.count_chunking_enabled_chats()
        except Exception as exc:
            logger.error("Admin chat settings back error: %s", exc, exc_info=True)
            await callback.answer("Не удалось получить список чатов.", show_alert=True)
            return
        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(
                build_admin_chat_settings_list_text(chats, page=0, chunking_enabled_chats=chunking_enabled_chats),
                parse_mode="HTML",
                reply_markup=build_admin_chat_settings_list_keyboard(chats, page=0),
                disable_web_page_preview=True,
            )
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX))
    async def admin_chat_settings_select_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        chat_id_value = (callback.data or "").removeprefix(ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX)
        try:
            target_chat_id = int(chat_id_value)
        except ValueError:
            await callback.answer("Неизвестный чат.", show_alert=True)
            return

        await refresh_admin_chat_settings_message(services, callback, target_chat_id)

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX))
    async def admin_chat_settings_leave_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        chat_id_value = (callback.data or "").removeprefix(ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX)
        try:
            target_chat_id = int(chat_id_value)
        except ValueError:
            await callback.answer("Неизвестный чат.", show_alert=True)
            return

        known_chat = await find_known_chat_by_id(services.db_engine, target_chat_id)
        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(
                build_admin_chat_leave_confirmation_text(target_chat_id, known_chat),
                parse_mode="HTML",
                reply_markup=build_admin_chat_leave_confirmation_keyboard(target_chat_id),
            )
        await callback.answer("Нужно подтвердить удаление.")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX))
    async def admin_chat_settings_leave_confirm_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        chat_id_value = (callback.data or "").removeprefix(ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX)
        try:
            target_chat_id = int(chat_id_value)
        except ValueError:
            await callback.answer("Неизвестный чат.", show_alert=True)
            return

        bot = getattr(callback, "bot", None)
        if bot is None:
            await callback.answer("Не удалось получить bot context.", show_alert=True)
            return
        try:
            await leave_admin_managed_chat(services, bot, target_chat_id)
        except Exception as exc:
            logger.warning(
                "Failed to leave chat from admin panel (chat_id=%s, error_type=%s)",
                target_chat_id,
                type(exc).__name__,
                exc_info=True,
            )
            await callback.answer("Не удалось удалить бота из чата.", show_alert=True)
            return

        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(
                build_admin_chat_removed_text(target_chat_id),
                parse_mode="HTML",
                reply_markup=build_admin_chat_removed_keyboard(target_chat_id),
            )
        await callback.answer("Бот удален из чата.")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX))
    async def admin_chat_settings_restore_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        chat_id_value = (callback.data or "").removeprefix(ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX)
        try:
            target_chat_id = int(chat_id_value)
        except ValueError:
            await callback.answer("Неизвестный чат.", show_alert=True)
            return

        await allow_admin_managed_chat_readd(services, target_chat_id)
        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(
                build_admin_chat_readd_allowed_text(target_chat_id),
                parse_mode="HTML",
                reply_markup=build_admin_chat_back_keyboard(),
            )
        await callback.answer("Повторное добавление разрешено.")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX))
    async def admin_chat_settings_model_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_admin_chat_setting_callback(callback.data or "", ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX)
        if parsed is None:
            await callback.answer("Неизвестная модель.", show_alert=True)
            return
        target_chat_id, model_index_value = parsed

        try:
            model_index = int(model_index_value)
            model_id = services.list_model_options()[model_index].model_id
        except (IndexError, ValueError):
            await callback.answer("Неизвестная модель.", show_alert=True)
            return

        try:
            active_model = await services.set_active_llm_model(target_chat_id, model_id)
        except KeyError:
            await callback.answer("Неизвестная модель.", show_alert=True)
            return
        except ValueError:
            await callback.answer("Для этой модели не задан API key.", show_alert=True)
            return

        generation_settings = await services.get_summary_generation_settings(target_chat_id)
        chunking_enabled = await services.is_chunking_enabled(target_chat_id)
        await edit_admin_chat_settings_message(
            services,
            callback,
            target_chat_id,
            active_model,
            generation_settings,
            chunking_enabled=chunking_enabled,
        )
        await callback.answer(f"Выбрано: {active_model.option.label}")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX))
    async def admin_chat_settings_tokens_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_admin_chat_setting_callback(callback.data or "", ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX)
        if parsed is None:
            await callback.answer("Неизвестный лимит токенов.", show_alert=True)
            return
        target_chat_id, token_value = parsed

        try:
            max_output_tokens = parse_summary_tokens_callback_value(token_value)
            generation_settings = await services.set_summary_max_output_tokens(target_chat_id, max_output_tokens)
        except ValueError:
            await callback.answer("Неизвестный лимит токенов.", show_alert=True)
            return

        active_model = await services.get_active_llm_model(target_chat_id)
        chunking_enabled = await services.is_chunking_enabled(target_chat_id)
        await edit_admin_chat_settings_message(
            services,
            callback,
            target_chat_id,
            active_model,
            generation_settings,
            chunking_enabled=chunking_enabled,
        )
        await callback.answer(f"Output tokens: {format_summary_tokens_setting(generation_settings)}")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX))
    async def admin_chat_settings_thinking_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_admin_chat_setting_callback(callback.data or "", ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX)
        if parsed is None:
            await callback.answer("Неизвестный режим thinking.", show_alert=True)
            return
        target_chat_id, thinking_mode = parsed

        try:
            generation_settings = await services.set_summary_thinking_mode(target_chat_id, thinking_mode)
        except ValueError:
            await callback.answer("Неизвестный режим thinking.", show_alert=True)
            return

        active_model = await services.get_active_llm_model(target_chat_id)
        chunking_enabled = await services.is_chunking_enabled(target_chat_id)
        await edit_admin_chat_settings_message(
            services,
            callback,
            target_chat_id,
            active_model,
            generation_settings,
            chunking_enabled=chunking_enabled,
        )
        await callback.answer(f"Thinking: {format_summary_thinking_mode(generation_settings)}")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX))
    async def admin_chat_settings_chunking_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_admin_chat_setting_callback(callback.data or "", ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX)
        if parsed is None:
            await callback.answer("Неизвестный режим chunking.", show_alert=True)
            return
        target_chat_id, enabled_value = parsed
        if enabled_value not in {"on", "off"}:
            await callback.answer("Неизвестный режим chunking.", show_alert=True)
            return

        enabled = await services.set_chunking_enabled(target_chat_id, enabled_value == "on")
        await refresh_admin_chat_settings_message(services, callback, target_chat_id)
        await callback.answer(f"Chunking: {'включен' if enabled else 'выключен'}")

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX))
    async def admin_chat_presentation_menu_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_admin_chat_setting_callback(callback.data or "", ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX)
        if parsed is None:
            await callback.answer("Неизвестная настройка.", show_alert=True)
            return
        target_chat_id, component = parsed
        if component not in PRESENTATION_COMPONENTS:
            await callback.answer("Неизвестная настройка.", show_alert=True)
            return
        settings = await services.get_summary_presentation_settings(target_chat_id)
        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(
                build_admin_presentation_option_text(target_chat_id, settings, component),
                parse_mode="HTML",
                reply_markup=build_admin_presentation_option_keyboard(
                    services,
                    target_chat_id,
                    settings,
                    component,
                ),
            )
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_PRESENTATION_SET_CALLBACK_PREFIX))
    async def admin_chat_presentation_set_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_admin_presentation_set_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Неизвестная настройка.", show_alert=True)
            return
        target_chat_id, component, value = parsed
        kwargs = {}
        if component == PRESENTATION_COMPONENT_STYLE:
            kwargs["style_id"] = value
        elif component == PRESENTATION_COMPONENT_TONE:
            kwargs["tone_id"] = value
        elif component == PRESENTATION_COMPONENT_AGGRESSIVENESS:
            try:
                kwargs["aggressiveness"] = int(value)
            except ValueError:
                await callback.answer("Неизвестный уровень.", show_alert=True)
                return

        try:
            await services.set_summary_presentation_settings(target_chat_id, **kwargs)
        except ValueError:
            await callback.answer("Неизвестное значение.", show_alert=True)
            return

        await refresh_admin_chat_settings_message(services, callback, target_chat_id)

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_PRESENTATION_BACK_CALLBACK_PREFIX))
    async def admin_chat_presentation_back_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return
        chat_id_value = (callback.data or "").removeprefix(ADMIN_CHAT_PRESENTATION_BACK_CALLBACK_PREFIX)
        try:
            target_chat_id = int(chat_id_value)
        except ValueError:
            await callback.answer("Неизвестный чат.", show_alert=True)
            return
        await refresh_admin_chat_settings_message(services, callback, target_chat_id)

    @dispatcher.callback_query(F.data.startswith(ADMIN_CHAT_PRESENTATION_RESET_CALLBACK_PREFIX))
    async def admin_chat_presentation_reset_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return
        chat_id_value = (callback.data or "").removeprefix(ADMIN_CHAT_PRESENTATION_RESET_CALLBACK_PREFIX)
        try:
            target_chat_id = int(chat_id_value)
        except ValueError:
            await callback.answer("Неизвестный чат.", show_alert=True)
            return
        await services.reset_summary_presentation_settings(target_chat_id)
        await refresh_admin_chat_settings_message(
            services,
            callback,
            target_chat_id,
            answer_text="Оформление пересказа сброшено.",
        )
