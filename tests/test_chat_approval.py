from types import SimpleNamespace

from sumbot.chat_approval import (
    CHAT_APPROVAL_LEAVE_ACTION,
    CHAT_APPROVAL_REVIEW_ACTION,
    build_chat_approval_callback_data,
    build_chat_approval_keyboard,
    build_chat_approval_key,
    build_chat_approval_notification,
    parse_chat_approval_callback_data,
)


def test_chat_approval_key_and_callback_data_are_chat_scoped():
    assert build_chat_approval_key(-1001) == "chat_approval_status:-1001"
    assert build_chat_approval_callback_data(CHAT_APPROVAL_LEAVE_ACTION, -1001) == "chat_approval:leave:-1001"
    assert build_chat_approval_callback_data(CHAT_APPROVAL_REVIEW_ACTION, -1001) == "chat_approval:review:-1001"


def test_parse_chat_approval_callback_data():
    assert parse_chat_approval_callback_data("chat_approval:leave:-1001") == ("leave", -1001)
    assert parse_chat_approval_callback_data("chat_approval:review:-1001") == ("review", -1001)
    assert parse_chat_approval_callback_data("chat_approval:approve:-1001") is None
    assert parse_chat_approval_callback_data("chat_approval:bad:-1001") is None
    assert parse_chat_approval_callback_data("chat_approval:leave:bad") is None


def test_build_chat_approval_keyboard_contains_review_and_leave():
    keyboard = build_chat_approval_keyboard(-1001)

    assert keyboard.inline_keyboard[0][0].text == "Просмотрено"
    assert keyboard.inline_keyboard[0][0].callback_data == "chat_approval:review:-1001"
    assert keyboard.inline_keyboard[0][1].text == "Удалить бота"
    assert keyboard.inline_keyboard[0][1].callback_data == "chat_approval:leave:-1001"


def test_build_chat_approval_notification_includes_chat_and_inviter():
    chat = SimpleNamespace(
        id=-1001,
        type="supergroup",
        title="Suspicious Chat",
        username="suspicious_chat",
    )
    inviter = SimpleNamespace(
        id=777,
        full_name="Alice Example",
        first_name="Alice",
        username="alice",
    )

    text = build_chat_approval_notification(chat, inviter=inviter)

    assert "Бота добавили в новый чат" in text
    assert "Suspicious Chat" in text
    assert "<code>-1001</code>" in text
    assert "https://t.me/suspicious_chat" in text
    assert "Alice Example" in text
    assert "<code>777</code>" in text
    assert "Бот уже работает в чате" in text
