import html

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sumbot.chat_registry import build_chat_snapshot

CHAT_APPROVAL_REDIS_KEY_PREFIX = "chat_approval_status"
CHAT_APPROVAL_STATUS_SEEN = "seen"
CHAT_APPROVAL_STATUS_REVIEWED = "reviewed"
CHAT_APPROVAL_STATUS_LEFT = "left"
CHAT_APPROVAL_STATUSES = frozenset(
    {
        CHAT_APPROVAL_STATUS_SEEN,
        CHAT_APPROVAL_STATUS_REVIEWED,
        CHAT_APPROVAL_STATUS_LEFT,
    }
)
CHAT_APPROVAL_CALLBACK_PREFIX = "chat_approval:"
CHAT_APPROVAL_REVIEW_ACTION = "review"
CHAT_APPROVAL_LEAVE_ACTION = "leave"


def build_chat_approval_key(chat_id: int) -> str:
    return f"{CHAT_APPROVAL_REDIS_KEY_PREFIX}:{chat_id}"


def build_chat_approval_callback_data(action: str, chat_id: int) -> str:
    if action not in {CHAT_APPROVAL_REVIEW_ACTION, CHAT_APPROVAL_LEAVE_ACTION}:
        raise ValueError(action)
    return f"{CHAT_APPROVAL_CALLBACK_PREFIX}{action}:{chat_id}"


def parse_chat_approval_callback_data(data: str | None) -> tuple[str, int] | None:
    if not data or not data.startswith(CHAT_APPROVAL_CALLBACK_PREFIX):
        return None
    payload = data.removeprefix(CHAT_APPROVAL_CALLBACK_PREFIX)
    action, separator, chat_id_value = payload.partition(":")
    if not separator or action not in {CHAT_APPROVAL_REVIEW_ACTION, CHAT_APPROVAL_LEAVE_ACTION}:
        return None
    try:
        return action, int(chat_id_value)
    except ValueError:
        return None


def build_chat_approval_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Просмотрено",
                    callback_data=build_chat_approval_callback_data(CHAT_APPROVAL_REVIEW_ACTION, chat_id),
                ),
                InlineKeyboardButton(
                    text="Удалить бота",
                    callback_data=build_chat_approval_callback_data(CHAT_APPROVAL_LEAVE_ACTION, chat_id),
                ),
            ]
        ]
    )


def build_chat_approval_notification(chat: types.Chat, inviter: types.User | None = None) -> str:
    snapshot = build_chat_snapshot(chat, bot_status=CHAT_APPROVAL_STATUS_SEEN)
    lines = [
        "🛡 <b>Бота добавили в новый чат</b>",
        "",
        f"<b>{html.escape(snapshot.title)}</b>",
        f"id: <code>{snapshot.chat_id}</code>",
        f"type: {html.escape(snapshot.chat_type)}",
        f"visibility: {'public' if snapshot.is_public else 'private'}",
    ]
    if snapshot.username:
        lines.append(f"username: @{html.escape(snapshot.username)}")
    if snapshot.public_link:
        lines.append(f"link: {html.escape(snapshot.public_link)}")
    if inviter is not None:
        inviter_name = inviter.full_name or inviter.first_name or "unknown"
        lines.extend(
            [
                "",
                "<b>Added by</b>",
                f"name: {html.escape(inviter_name)}",
                f"id: <code>{inviter.id}</code>",
            ]
        )
        if inviter.username:
            lines.append(f"username: @{html.escape(inviter.username)}")
    lines.extend(
        [
            "",
            "Бот уже работает в чате. Нажми «Просмотрено» или удали его, если чат выглядит подозрительно.",
        ]
    )
    return "\n".join(lines)
