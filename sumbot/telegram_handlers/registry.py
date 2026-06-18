import logging

from aiogram import Dispatcher, F, types
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated

import config
from sumbot.chat_approval import (
    CHAT_APPROVAL_CALLBACK_PREFIX,
    CHAT_APPROVAL_LEAVE_ACTION,
    CHAT_APPROVAL_STATUS_LEFT,
    CHAT_APPROVAL_STATUS_REVIEWED,
    CHAT_APPROVAL_STATUS_SEEN,
    CHAT_APPROVAL_REVIEW_ACTION,
    build_chat_approval_keyboard,
    build_chat_approval_notification,
    parse_chat_approval_callback_data,
)
from sumbot.chat_registry import normalize_member_status, save_chat_snapshot
from sumbot.constants import ANALYTICS_CHAT_ID, DEBUG_USER_ID
from sumbot.services import BotServices
from sumbot.engagement import start_chat_onboarding
from sumbot.telegram_handlers.admin_chat_handlers import register_admin_chat_handlers
from sumbot.telegram_handlers.debug_handlers import register_debug_handlers
from sumbot.telegram_handlers.digest import register_digest_handlers
from sumbot.telegram_handlers.feedback_details import register_feedback_handlers
from sumbot.telegram_handlers.prompt_profile_handlers import register_prompt_profile_handlers
from sumbot.telegram_handlers.reminders import register_reminder_handlers
from sumbot.telegram_handlers.summary import register_summary_handlers

logger = logging.getLogger("SumBot.telegram_handlers.registry")


def register_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    register_lifecycle_handlers(dispatcher, services)
    register_debug_handlers(dispatcher, services)
    register_prompt_profile_handlers(dispatcher, services)
    register_admin_chat_handlers(dispatcher, services)
    register_reminder_handlers(dispatcher, services)
    register_digest_handlers(dispatcher, services)
    register_summary_handlers(dispatcher, services)
    register_feedback_handlers(dispatcher, services)


def register_lifecycle_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.my_chat_member()
    async def on_bot_added(event: ChatMemberUpdated) -> None:
        old_status = normalize_member_status(event.old_chat_member.status)
        new_status = normalize_member_status(event.new_chat_member.status)
        logger.info(
            "Bot chat member status changed "
            "(chat_id=%s, chat_type=%s, old_status=%s, new_status=%s)",
            event.chat.id,
            event.chat.type,
            old_status,
            new_status,
        )
        await save_chat_snapshot(
            services.db_engine,
            event.chat,
            bot_status=new_status,
        )

        became_member = new_status in {ChatMemberStatus.MEMBER.value, ChatMemberStatus.ADMINISTRATOR.value}
        was_absent = old_status in {ChatMemberStatus.LEFT.value, ChatMemberStatus.KICKED.value}

        if became_member and was_absent:
            saved_approval_status = await services.get_saved_chat_approval_status(event.chat.id)
            if saved_approval_status == CHAT_APPROVAL_STATUS_LEFT:
                logger.warning("Bot re-added to previously rejected chat; leaving immediately (chat_id=%s)", event.chat.id)
                try:
                    await event.bot.leave_chat(event.chat.id)
                except Exception as exc:
                    logger.warning(
                        "Failed to leave previously rejected chat (chat_id=%s, error_type=%s)",
                        event.chat.id,
                        type(exc).__name__,
                        exc_info=True,
                    )
                return

            await services.set_chat_approval_status(event.chat.id, CHAT_APPROVAL_STATUS_SEEN)
            await start_chat_onboarding(services.redis, event.chat.id)
            await event.bot.send_message(event.chat.id, config.WELCOME_TEXT, parse_mode="Markdown")
            await notify_chat_join(event)
            logger.info("Welcome message sent after bot joined chat (chat_id=%s)", event.chat.id)

        if new_status in {ChatMemberStatus.LEFT.value, ChatMemberStatus.KICKED.value}:
            await services.set_chat_approval_status(event.chat.id, CHAT_APPROVAL_STATUS_LEFT)

    @dispatcher.callback_query(F.data.startswith(CHAT_APPROVAL_CALLBACK_PREFIX))
    async def chat_approval_callback(callback: types.CallbackQuery) -> None:
        if callback.from_user.id != DEBUG_USER_ID:
            await callback.answer("Эта настройка недоступна.", show_alert=True)
            return

        parsed = parse_chat_approval_callback_data(callback.data)
        if parsed is None:
            await callback.answer("Неизвестное действие.", show_alert=True)
            return
        action, target_chat_id = parsed

        if action == CHAT_APPROVAL_REVIEW_ACTION:
            await services.set_chat_approval_status(target_chat_id, CHAT_APPROVAL_STATUS_REVIEWED)
            await remove_chat_approval_keyboard(callback)
            await callback.answer("Чат отмечен как просмотренный.")
            return

        if action == CHAT_APPROVAL_LEAVE_ACTION:
            bot = get_callback_bot(callback)
            if bot is None:
                await callback.answer("Не удалось получить bot context.", show_alert=True)
                return
            try:
                await bot.leave_chat(target_chat_id)
            except Exception as exc:
                logger.warning(
                    "Failed to leave rejected chat (chat_id=%s, error_type=%s)",
                    target_chat_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                await callback.answer("Не удалось выйти из чата.", show_alert=True)
                return
            await services.set_chat_approval_status(target_chat_id, CHAT_APPROVAL_STATUS_LEFT)
            await edit_chat_approval_message(callback, f"Left chat <code>{target_chat_id}</code>.")
            await callback.answer("Бот вышел из чата.")

async def notify_chat_join(event: ChatMemberUpdated) -> None:
    try:
        await event.bot.send_message(
            ANALYTICS_CHAT_ID,
            build_chat_approval_notification(event.chat, inviter=event.from_user),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=build_chat_approval_keyboard(event.chat.id),
        )
    except Exception as exc:
        logger.error(
            "Failed to notify analytics chat about bot chat join (chat_id=%s, error_type=%s)",
            event.chat.id,
            type(exc).__name__,
            exc_info=True,
        )


async def edit_chat_approval_message(callback: types.CallbackQuery, text: str) -> None:
    if callback.message is not None and hasattr(callback.message, "edit_text"):
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=None)
        except Exception as exc:
            logger.warning("Failed to edit chat approval message: %s", exc)


async def remove_chat_approval_keyboard(callback: types.CallbackQuery) -> None:
    if callback.message is not None and hasattr(callback.message, "edit_reply_markup"):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Failed to remove chat approval keyboard: %s", exc)


def get_callback_bot(callback: types.CallbackQuery) -> object | None:
    bot = getattr(callback, "bot", None)
    if bot is not None:
        return bot
    message = getattr(callback, "message", None)
    return getattr(message, "bot", None)
