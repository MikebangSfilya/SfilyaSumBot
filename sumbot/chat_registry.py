import html
import logging
from dataclasses import dataclass
from typing import Any

from aiogram import types
from aiogram.enums import ChatMemberStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger("SumBot.chat_registry")

PUBLIC_LINK_CHAT_TYPES = {"group", "supergroup", "channel"}


@dataclass(slots=True)
class ChatSnapshot:
    chat_id: int
    chat_type: str
    title: str
    username: str | None
    is_public: bool
    public_link: str | None
    bot_status: str


@dataclass(slots=True)
class KnownBotChat:
    chat_id: int
    chat_type: str
    title: str
    username: str | None
    is_public: bool
    public_link: str | None
    bot_status: str
    first_seen_at: Any
    last_seen_at: Any


def build_chat_snapshot(chat: types.Chat, bot_status: str = "seen") -> ChatSnapshot:
    chat_type = normalize_chat_type(getattr(chat, "type", None))
    username = None if chat_type == "private" else normalize_username(getattr(chat, "username", None))
    is_public = is_public_chat(chat_type, username)

    return ChatSnapshot(
        chat_id=chat.id,
        chat_type=chat_type,
        title="private chat" if chat_type == "private" else get_chat_display_title(chat),
        username=username,
        is_public=is_public,
        public_link=f"https://t.me/{username}" if is_public else None,
        bot_status=bot_status,
    )


def normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    return username.removeprefix("@")


def normalize_chat_type(chat_type: Any) -> str:
    if hasattr(chat_type, "value"):
        return str(chat_type.value)
    if chat_type is None:
        return "unknown"
    return str(chat_type)


def normalize_member_status(status: ChatMemberStatus | str) -> str:
    if hasattr(status, "value"):
        return str(status.value)
    return str(status)


def is_public_chat(chat_type: str, username: str | None) -> bool:
    return bool(username) and chat_type in PUBLIC_LINK_CHAT_TYPES


def get_chat_display_title(chat: types.Chat) -> str:
    title = getattr(chat, "title", None)
    if title:
        return title

    full_name = getattr(chat, "full_name", None)
    if full_name:
        return full_name

    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    if first_name or last_name:
        return " ".join(part for part in (first_name, last_name) if part)

    username = getattr(chat, "username", None)
    if username:
        return f"@{normalize_username(username)}"

    return f"chat {chat.id}"


async def save_chat_snapshot(
    db_engine: AsyncEngine | None,
    chat: types.Chat,
    bot_status: str = "seen",
) -> None:
    if not db_engine:
        logger.debug("Skipping chat snapshot because database is disabled (chat_id=%s)", chat.id)
        return

    try:
        snapshot = build_chat_snapshot(chat, bot_status=bot_status)
        async with db_engine.begin() as conn:
            await upsert_bot_chat(conn, snapshot)
        logger.debug(
            "Chat snapshot saved (chat_id=%s, chat_type=%s, is_public=%s, username=%s, bot_status=%s)",
            snapshot.chat_id,
            snapshot.chat_type,
            snapshot.is_public,
            snapshot.username,
            snapshot.bot_status,
        )
    except Exception as exc:
        logger.error("DB Chat Registry Error: %s", exc, exc_info=True)


async def upsert_bot_chat(conn: AsyncConnection, snapshot: ChatSnapshot) -> None:
    await conn.execute(
        text("""
            INSERT INTO bot_chats (
                chat_id,
                chat_type,
                title,
                username,
                is_public,
                public_link,
                bot_status,
                first_seen_at,
                last_seen_at,
                updated_at
            )
            VALUES (
                :chat_id,
                :chat_type,
                :title,
                :username,
                :is_public,
                :public_link,
                :bot_status,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (chat_id)
            DO UPDATE SET
                chat_type = EXCLUDED.chat_type,
                title = EXCLUDED.title,
                username = EXCLUDED.username,
                is_public = EXCLUDED.is_public,
                public_link = EXCLUDED.public_link,
                bot_status = EXCLUDED.bot_status,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
        """),
        {
            "chat_id": snapshot.chat_id,
            "chat_type": snapshot.chat_type,
            "title": snapshot.title,
            "username": snapshot.username,
            "is_public": snapshot.is_public,
            "public_link": snapshot.public_link,
            "bot_status": snapshot.bot_status,
        },
    )


async def fetch_known_bot_chats(
    db_engine: AsyncEngine | None,
    active_only: bool = True,
) -> list[KnownBotChat]:
    if not db_engine:
        logger.warning("Known bot chats requested without database connection.")
        return []

    where_clause = ""
    if active_only:
        where_clause = "WHERE bot_status NOT IN ('left', 'kicked')"

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT
                    chat_id,
                    chat_type,
                    title,
                    username,
                    is_public,
                    public_link,
                    bot_status,
                    first_seen_at,
                    last_seen_at
                FROM bot_chats
                {where_clause}
                ORDER BY last_seen_at DESC, chat_id ASC
            """)
        )
        chats = [row_to_known_bot_chat(row) for row in result.fetchall()]
        logger.info("Fetched known bot chats (active_only=%s, count=%s)", active_only, len(chats))
        return chats


def row_to_known_bot_chat(row: Any) -> KnownBotChat:
    data = row._mapping if hasattr(row, "_mapping") else row
    return KnownBotChat(
        chat_id=data["chat_id"],
        chat_type=data["chat_type"],
        title=data["title"],
        username=data["username"],
        is_public=data["is_public"],
        public_link=data["public_link"],
        bot_status=data["bot_status"],
        first_seen_at=data["first_seen_at"],
        last_seen_at=data["last_seen_at"],
    )


def format_known_bot_chats(chats: list[KnownBotChat], limit: int = 50) -> str:
    if not chats:
        return "Нет сохраненных чатов. Они появятся после новых сообщений или событий."

    shown_chats = chats[:limit]
    public_count = sum(1 for chat in chats if chat.is_public)
    private_count = len(chats) - public_count
    lines = [f"Чаты, где замечен бот: {len(chats)} (public: {public_count}, private: {private_count})"]
    if len(chats) > limit:
        lines.append(f"Показаны последние {limit}.")

    for index, chat in enumerate(shown_chats, start=1):
        title = html.escape(chat.title)
        line = (
            f"\n{index}. <b>{title}</b>\n"
            f"id: <code>{chat.chat_id}</code>\n"
            f"type: {html.escape(chat.chat_type)}\n"
            f"visibility: {'public' if chat.is_public else 'private'}\n"
            f"bot_status: {html.escape(chat.bot_status)}"
        )
        if chat.public_link:
            line += f"\nlink: {html.escape(chat.public_link)}"
        if chat.username:
            line += f"\nusername: @{html.escape(chat.username)}"
        lines.append(line)

    return "\n".join(lines)
