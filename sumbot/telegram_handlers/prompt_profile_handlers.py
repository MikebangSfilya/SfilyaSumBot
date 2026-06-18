import logging

from aiogram import Dispatcher, F, types
from aiogram.filters import Command

from sumbot.chat_registry import normalize_chat_type, save_chat_snapshot
from sumbot.services import BotServices
from sumbot.telegram_handlers.common import is_debug_user
from sumbot.telegram_handlers.debug_constants import (
    DEBUG_MESSAGE_AUTO_DELETE_SECONDS,
    PRESENTATION_DELETE_CALLBACK_DATA,
    PRESENTATION_MAIN_CALLBACK_DATA,
    PRESENTATION_MENU_CALLBACK_PREFIX,
    PRESENTATION_OPEN_CALLBACK_DATA,
    PRESENTATION_RESET_CALLBACK_DATA,
    PRESENTATION_SET_CALLBACK_PREFIX,
)
from sumbot.telegram_handlers.debug_message_lifecycle import (
    build_debug_command_message_key,
    delete_debug_message_pair,
)
from sumbot.telegram_handlers.prompt_profile_panel import (
    PRESENTATION_COMPONENT_AGGRESSIVENESS,
    PRESENTATION_COMPONENT_STYLE,
    PRESENTATION_COMPONENT_TONE,
    PRESENTATION_COMPONENTS,
    build_presentation_option_info,
    build_presentation_option_keyboard,
    build_presentation_panel_info,
    build_presentation_panel_keyboard,
    parse_presentation_set_callback,
)

logger = logging.getLogger("SumBot.telegram_handlers.presentation")


def register_prompt_profile_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.message(Command("prompt", "prompt_builder"))
    async def prompt_profile_cmd(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)
        if not await can_manage_presentation(message.bot, message.chat, message.from_user):
            await message.answer("Настраивать пересказ могут только администраторы группы.")
            return

        settings = await services.get_summary_presentation_settings(message.chat.id)
        prompt_message = await message.answer(
            build_presentation_panel_info(message.chat, settings),
            parse_mode="HTML",
            reply_markup=build_presentation_panel_keyboard(settings),
        )
        await services.redis.set(
            build_debug_command_message_key(message.chat.id, prompt_message.message_id),
            str(message.message_id),
            ex=DEBUG_MESSAGE_AUTO_DELETE_SECONDS,
        )

    @dispatcher.callback_query(F.data.in_({PRESENTATION_OPEN_CALLBACK_DATA, PRESENTATION_MAIN_CALLBACK_DATA}))
    async def presentation_main_callback(callback: types.CallbackQuery) -> None:
        chat = getattr(callback.message, "chat", None)
        if not await _ensure_access(callback, chat, services):
            return
        settings = await services.get_summary_presentation_settings(chat.id)
        await callback.message.edit_text(
            build_presentation_panel_info(chat, settings),
            parse_mode="HTML",
            reply_markup=build_presentation_panel_keyboard(settings),
        )
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith(PRESENTATION_MENU_CALLBACK_PREFIX))
    async def presentation_menu_callback(callback: types.CallbackQuery) -> None:
        chat = getattr(callback.message, "chat", None)
        if not await _ensure_access(callback, chat, services):
            return
        component = (callback.data or "").removeprefix(PRESENTATION_MENU_CALLBACK_PREFIX)
        if component not in PRESENTATION_COMPONENTS:
            await callback.answer("Неизвестная настройка.", show_alert=True)
            return
        settings = await services.get_summary_presentation_settings(chat.id)
        await callback.message.edit_text(
            build_presentation_option_info(chat, settings, component),
            parse_mode="HTML",
            reply_markup=build_presentation_option_keyboard(services, settings, component),
        )
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith(PRESENTATION_SET_CALLBACK_PREFIX))
    async def presentation_set_callback(callback: types.CallbackQuery) -> None:
        chat = getattr(callback.message, "chat", None)
        if not await _ensure_access(callback, chat, services):
            return
        parsed = parse_presentation_set_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Неизвестная настройка.", show_alert=True)
            return
        component, value = parsed
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
            settings = await services.set_summary_presentation_settings(chat.id, **kwargs)
        except ValueError:
            await callback.answer("Неизвестное значение.", show_alert=True)
            return
        await callback.message.edit_text(
            build_presentation_panel_info(chat, settings),
            parse_mode="HTML",
            reply_markup=build_presentation_panel_keyboard(settings),
        )
        await callback.answer("Настройка сохранена")

    @dispatcher.callback_query(F.data == PRESENTATION_RESET_CALLBACK_DATA)
    async def presentation_reset_callback(callback: types.CallbackQuery) -> None:
        chat = getattr(callback.message, "chat", None)
        if not await _ensure_access(callback, chat, services):
            return
        settings = await services.reset_summary_presentation_settings(chat.id)
        await callback.message.edit_text(
            build_presentation_panel_info(chat, settings),
            parse_mode="HTML",
            reply_markup=build_presentation_panel_keyboard(settings),
        )
        await callback.answer("Настройки сброшены")

    @dispatcher.callback_query(F.data == PRESENTATION_DELETE_CALLBACK_DATA)
    async def presentation_delete_callback(callback: types.CallbackQuery) -> None:
        chat = getattr(callback.message, "chat", None)
        if not await _ensure_access(callback, chat, services):
            return
        await callback.answer("Удаляю панель настроек.")
        await delete_debug_message_pair(services, callback.message)


async def _ensure_access(
    callback: types.CallbackQuery,
    chat: types.Chat | None,
    services: BotServices,
) -> bool:
    if chat is None or callback.message is None or not hasattr(callback.message, "edit_text"):
        await callback.answer("Чат уже недоступен.", show_alert=True)
        return False
    if not await can_manage_presentation(callback.bot, chat, callback.from_user):
        await callback.answer("Настраивать пересказ могут только администраторы группы.", show_alert=True)
        return False
    await save_chat_snapshot(services.db_engine, chat)
    return True


async def can_manage_presentation(bot: object, chat: types.Chat, user: types.User | None) -> bool:
    if is_debug_user(user):
        return True
    if user is None or normalize_chat_type(chat.type) not in {"group", "supergroup"}:
        return False
    try:
        member = await bot.get_chat_member(chat.id, user.id)
    except Exception:
        logger.exception(
            "Failed to check presentation permissions (chat_id=%s, user_id=%s)",
            chat.id,
            user.id,
        )
        return False
    status = getattr(member.status, "value", member.status)
    return str(status) in {"administrator", "creator"}
