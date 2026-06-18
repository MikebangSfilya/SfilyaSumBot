from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sumbot.chat_registry import (
    ChatSnapshot,
    KnownBotChat,
    build_chat_snapshot,
    fetch_known_bot_chats,
    format_known_bot_chats,
    normalize_member_status,
    save_chat_snapshot,
    upsert_bot_chat,
)


class FakeResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return FakeResult(self.rows)


class FakeBegin:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return FakeBegin(self.conn)


def test_build_chat_snapshot_marks_public_supergroup_and_builds_link():
    chat = SimpleNamespace(
        id=-1001,
        type="supergroup",
        title="Public Chat",
        username="@public_chat",
    )

    snapshot = build_chat_snapshot(chat, bot_status="administrator")

    assert snapshot == ChatSnapshot(
        chat_id=-1001,
        chat_type="supergroup",
        title="Public Chat",
        username="public_chat",
        is_public=True,
        public_link="https://t.me/public_chat",
        bot_status="administrator",
    )


def test_build_chat_snapshot_does_not_treat_private_user_as_public_chat():
    chat = SimpleNamespace(
        id=123,
        type="private",
        title=None,
        username="alice",
        first_name="Alice",
        last_name="Tester",
    )

    snapshot = build_chat_snapshot(chat)

    assert snapshot.title == "private chat"
    assert snapshot.username is None
    assert snapshot.is_public is False
    assert snapshot.public_link is None


@pytest.mark.asyncio
async def test_save_chat_snapshot_persists_private_chats():
    conn = FakeConnection()
    engine = FakeEngine(conn)
    chat = SimpleNamespace(
        id=123,
        type="private",
        title=None,
        username="alice",
        first_name="Alice",
        last_name="Tester",
    )

    await save_chat_snapshot(engine, chat)

    assert len(conn.calls) == 1
    assert conn.calls[0][1]["chat_id"] == 123
    assert conn.calls[0][1]["chat_type"] == "private"
    assert conn.calls[0][1]["title"] == "private chat"
    assert conn.calls[0][1]["username"] is None
    assert conn.calls[0][1]["is_public"] is False
    assert conn.calls[0][1]["public_link"] is None


@pytest.mark.asyncio
async def test_save_chat_snapshot_persists_private_groups_without_username():
    conn = FakeConnection()
    engine = FakeEngine(conn)
    chat = SimpleNamespace(
        id=-1002,
        type="supergroup",
        title="Private Group",
        username=None,
    )

    await save_chat_snapshot(engine, chat)

    assert len(conn.calls) == 1
    assert conn.calls[0][1]["chat_id"] == -1002
    assert conn.calls[0][1]["chat_type"] == "supergroup"
    assert conn.calls[0][1]["title"] == "Private Group"
    assert conn.calls[0][1]["is_public"] is False
    assert conn.calls[0][1]["public_link"] is None


@pytest.mark.asyncio
async def test_save_chat_snapshot_persists_public_chats():
    conn = FakeConnection()
    engine = FakeEngine(conn)
    chat = SimpleNamespace(
        id=-1001,
        type="supergroup",
        title="Public Chat",
        username="public_chat",
    )

    await save_chat_snapshot(engine, chat, bot_status="member")

    assert len(conn.calls) == 1
    assert conn.calls[0][1]["chat_id"] == -1001
    assert conn.calls[0][1]["is_public"] is True


@pytest.mark.asyncio
async def test_upsert_bot_chat_uses_chat_id_conflict_target():
    conn = FakeConnection()
    snapshot = ChatSnapshot(
        chat_id=-1001,
        chat_type="supergroup",
        title="Public Chat",
        username="public_chat",
        is_public=True,
        public_link="https://t.me/public_chat",
        bot_status="member",
    )

    await upsert_bot_chat(conn, snapshot)

    statement, params = conn.calls[0]
    assert "INSERT INTO bot_chats" in statement
    assert "ON CONFLICT (chat_id)" in statement
    assert params == {
        "chat_id": -1001,
        "chat_type": "supergroup",
        "title": "Public Chat",
        "username": "public_chat",
        "is_public": True,
        "public_link": "https://t.me/public_chat",
        "bot_status": "member",
    }


@pytest.mark.asyncio
async def test_fetch_known_bot_chats_returns_active_rows():
    seen_at = datetime(2026, 5, 21, 22, 50, tzinfo=timezone.utc)
    conn = FakeConnection(
        rows=[
            {
                "chat_id": -1001,
                "chat_type": "supergroup",
                "title": "Public Chat",
                "username": "public_chat",
                "is_public": True,
                "public_link": "https://t.me/public_chat",
                "bot_status": "member",
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
        ]
    )
    engine = FakeEngine(conn)

    chats = await fetch_known_bot_chats(engine, active_only=True)

    assert chats == [
        KnownBotChat(
            chat_id=-1001,
            chat_type="supergroup",
            title="Public Chat",
            username="public_chat",
            is_public=True,
            public_link="https://t.me/public_chat",
            bot_status="member",
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
    ]
    assert "WHERE bot_status NOT IN ('left', 'kicked')" in conn.calls[0][0]
    assert "is_public = TRUE" not in conn.calls[0][0]


def test_format_known_bot_chats_includes_public_and_private_chats():
    chats = [
        KnownBotChat(
            chat_id=-1001,
            chat_type="supergroup",
            title="Public Chat",
            username="public_chat",
            is_public=True,
            public_link="https://t.me/public_chat",
            bot_status="member",
            first_seen_at=None,
            last_seen_at=None,
        ),
        KnownBotChat(
            chat_id=-1002,
            chat_type="supergroup",
            title="Private Group",
            username=None,
            is_public=False,
            public_link=None,
            bot_status="seen",
            first_seen_at=None,
            last_seen_at=None,
        ),
    ]

    text = format_known_bot_chats(chats)

    assert "Чаты, где замечен бот: 2 (public: 1, private: 1)" in text
    assert "visibility: public" in text
    assert "visibility: private" in text
    assert "link: https://t.me/public_chat" in text
    assert "Private Group" in text


def test_normalize_member_status_reads_enum_value():
    assert normalize_member_status(SimpleNamespace(value="administrator")) == "administrator"
