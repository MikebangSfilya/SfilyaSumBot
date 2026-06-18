import logging

from aiogram import types

from sumbot.chat_approval import CHAT_APPROVAL_STATUS_LEFT, CHAT_APPROVAL_STATUS_REVIEWED
from sumbot.chat_registry import KnownBotChat, fetch_known_bot_chats
from sumbot.chunks import get_chunk_runtime_stats
from sumbot.services import ActiveLlmModel, BotServices, SummaryGenerationSettings
from sumbot.telegram_handlers.admin_chat_panel import (
    build_admin_chat_settings_info,
    build_admin_chat_settings_keyboard,
)

logger = logging.getLogger("SumBot.telegram_handlers.debug")


async def find_known_chat_by_id(db_engine: object | None, target_chat_id: int) -> KnownBotChat | None:
    if not db_engine:
        return None
    try:
        chats = await fetch_known_bot_chats(db_engine, active_only=False)
    except Exception as exc:
        logger.warning(
            "Failed to fetch known chat for admin settings (chat_id=%s, error_type=%s)",
            target_chat_id,
            type(exc).__name__,
            exc_info=True,
        )
        return None
    return next((chat for chat in chats if chat.chat_id == target_chat_id), None)


async def refresh_admin_chat_settings_message(
    services: BotServices,
    callback: types.CallbackQuery,
    target_chat_id: int,
    *,
    answer_text: str = "Открыты настройки чата.",
) -> None:
    active_model = await services.get_active_llm_model(target_chat_id)
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
    await callback.answer(answer_text)


async def edit_admin_chat_settings_message(
    services: BotServices,
    callback: types.CallbackQuery,
    target_chat_id: int,
    active_model: ActiveLlmModel,
    generation_settings: SummaryGenerationSettings,
    *,
    chunking_enabled: bool,
) -> None:
    known_chat = await find_known_chat_by_id(services.db_engine, target_chat_id)
    chat_approval_status = await services.get_chat_approval_status(target_chat_id)
    chunk_stats = await get_chunk_runtime_stats(services.redis, target_chat_id)
    chunking_enabled_chats = await services.count_chunking_enabled_chats()
    presentation_settings = await services.get_summary_presentation_settings(target_chat_id)
    if callback.message is not None and hasattr(callback.message, "edit_text"):
        await callback.message.edit_text(
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


async def leave_admin_managed_chat(services: BotServices, bot: object, target_chat_id: int) -> None:
    await bot.leave_chat(target_chat_id)
    await services.set_chat_approval_status(target_chat_id, CHAT_APPROVAL_STATUS_LEFT)


async def allow_admin_managed_chat_readd(services: BotServices, target_chat_id: int) -> None:
    await services.set_chat_approval_status(target_chat_id, CHAT_APPROVAL_STATUS_REVIEWED)
