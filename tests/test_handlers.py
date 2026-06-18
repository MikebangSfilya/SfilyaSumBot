import json
from types import SimpleNamespace

from aiogram import types
import pytest

from sumbot.constants import (
    DEFAULT_SUMMARY_PERIOD_SECONDS,
    DEBUG_USER_ID,
    FEEDBACK_DETAILS_DELETE_DELAY_SECONDS,
    RATE_LIMIT_SECONDS,
)
from sumbot.chat_registry import KnownBotChat
from sumbot.telegram_handlers.admin_chat_panel import (
    ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA,
    ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX,
    ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX,
    build_admin_presentation_option_keyboard,
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
)
from sumbot.telegram_handlers.debug_constants import (
    CHUNKING_SETTINGS_CALLBACK_PREFIX,
    DEBUG_DELETE_CALLBACK_DATA,
    PRESENTATION_DELETE_CALLBACK_DATA,
    PRESENTATION_MENU_CALLBACK_PREFIX,
    PRESENTATION_OPEN_CALLBACK_DATA,
    PRESENTATION_RESET_CALLBACK_DATA,
    PRESENTATION_SET_CALLBACK_PREFIX,
    SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX,
    SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX,
)
from sumbot.telegram_handlers.debug_panel import (
    build_debug_info,
    build_llm_model_settings_keyboard,
)
from sumbot.telegram_handlers.prompt_profile_panel import (
    PRESENTATION_COMPONENT_STYLE,
    build_presentation_option_keyboard,
    build_presentation_panel_info,
    build_presentation_panel_keyboard,
)
from sumbot.telegram_handlers.prompt_profile_handlers import can_manage_presentation
from sumbot.telegram_handlers.debug_message_lifecycle import (
    build_debug_command_message_key,
    delete_debug_message_pair,
)
from sumbot.telegram_handlers.debug_stats import count_recent_logs
from sumbot.telegram_handlers.feedback_details import (
    build_feedback_details_prompt,
    delete_feedback_prompt_on_timeout,
    handle_summary_feedback_callback,
    handle_summary_feedback_details_callback,
    handle_pending_feedback_details,
)
from sumbot.telegram_handlers.common import remove_reply_markup_after_delay
from sumbot.telegram_handlers.reminders import (
    CHAT_UPDATE_REMINDER_TEXT,
    PROMPT_SETTINGS_ANNOUNCEMENT_TEXT,
    ChatReminderResult,
    build_chat_update_reminder_key,
    build_prompt_settings_announcement_key,
    send_chat_update_reminders,
    send_prompt_settings_announcements,
)
from sumbot.telegram_handlers.registry import notify_chat_join
from sumbot.telegram_handlers.summary import (
    SummaryRequest,
    acquire_summary_rate_limit,
    build_summary_message_variants,
    choose_dynamic_summary_example,
    fetch_messages_for_summary_request,
    generate_chunk_native_summary_with_fallbacks,
    generate_summary_with_fallbacks,
    ensure_enough_messages,
    parse_summary_request,
)
from sumbot.llm import SummaryResult
from sumbot.prompt_builder import build_presentation_settings, load_style_catalog, load_tone_catalog
from sumbot.services import ActiveLlmModel, LlmModelOption, SummaryGenerationSettings
from sumbot.chunks import ChunkRuntimeStats, ChunkEvent, ChunkParticipant, ChunkSummaryRecord
from sumbot.summary_assembly import SummarySourceBundle


class FakeRedis:
    def __init__(self):
        self.calls = []
        self.storage = {}

    async def set(self, key, value, nx=False, ex=None):
        self.calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.storage:
            return False
        self.storage[key] = value
        return True

    async def get(self, key):
        return self.storage.get(key)

    async def delete(self, key):
        self.storage.pop(key, None)


def _presentation(
    style_id: str = "classic_chat_storyteller",
    tone_id: str = "ironic",
    aggressiveness: int = 2,
):
    return build_presentation_settings(
        load_style_catalog(),
        load_tone_catalog(),
        style_id=style_id,
        tone_id=tone_id,
        aggressiveness=aggressiveness,
    )


class FakeMessage:
    def __init__(self, text=None, user_id=7, reply_to_message_id=None, message_id=100):
        self.chat = SimpleNamespace(id=42)
        self.message_id = message_id
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.reply_to_message = (
            SimpleNamespace(message_id=reply_to_message_id)
            if reply_to_message_id is not None
            else None
        )
        self.answers = []
        self.answer_messages = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        answer_message = SimpleNamespace(
            message_id=1000 + len(self.answer_messages),
            text=text,
            kwargs=kwargs,
        )
        self.answer_messages.append(answer_message)
        return answer_message


class FakeCallback:
    def __init__(self, data, message=None, user_id=7):
        self.data = data
        self.message = message or FakeMessage()
        self.from_user = types.User(id=user_id, is_bot=False, first_name="Alice")
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_remove_reply_markup_after_delay_clears_inline_keyboard(monkeypatch):
    class FakeMarkupMessage:
        def __init__(self):
            self.reply_markup_updates = []

        async def edit_reply_markup(self, reply_markup=None):
            self.reply_markup_updates.append(reply_markup)

    message = FakeMarkupMessage()

    async def fake_sleep(delay_seconds):
        return None

    import sumbot.telegram_handlers.common as common_handlers

    monkeypatch.setattr(common_handlers.asyncio, "sleep", fake_sleep)

    await remove_reply_markup_after_delay(message, delay_seconds=900)

    assert message.reply_markup_updates == [None]


class FakeBot:
    def __init__(self, failing_chat_ids=None):
        self.failing_chat_ids = set(failing_chat_ids or ())
        self.sent_messages = []

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.failing_chat_ids:
            raise RuntimeError("send failed")
        self.sent_messages.append((chat_id, text, kwargs))


class FakeMembershipBot:
    def __init__(self, status="member", fail=False):
        self.status = status
        self.fail = fail
        self.calls = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        if self.fail:
            raise RuntimeError("membership lookup failed")
        return SimpleNamespace(status=self.status)


async def _async_result(value):
    return value


@pytest.mark.asyncio
async def test_notify_chat_join_sends_to_analytics_chat(monkeypatch):
    import sumbot.telegram_handlers.registry as registry_handlers

    monkeypatch.setattr(registry_handlers, "ANALYTICS_CHAT_ID", -100777)
    bot = FakeBot()
    event = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-1001, type="supergroup", title="New Chat", username="new_chat"),
        from_user=SimpleNamespace(id=42, full_name="Alice", username="alice"),
    )

    await notify_chat_join(event)

    assert bot.sent_messages[0][0] == -100777
    assert "New Chat" in bot.sent_messages[0][1]
    assert bot.sent_messages[0][2]["parse_mode"] == "HTML"
    assert bot.sent_messages[0][2]["reply_markup"] is not None


def test_parse_summary_request_defaults_to_24_hours():
    request = parse_summary_request("/summary")

    assert request == SummaryRequest(
        limit_messages=None,
        time_limit_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS,
    )


def test_parse_summary_request_accepts_message_limit():
    request = parse_summary_request("/summary 25")

    assert request == SummaryRequest(limit_messages=25, time_limit_seconds=None)


@pytest.mark.asyncio
async def test_acquire_summary_rate_limit_sets_redis_key():
    redis = FakeRedis()
    services = SimpleNamespace(redis=redis)

    acquired = await acquire_summary_rate_limit(services, user_id=7, chat_id=42)

    assert acquired is True
    assert redis.calls == [
        {"key": "rate_limit:7:42", "value": "1", "nx": True, "ex": RATE_LIMIT_SECONDS}
    ]


@pytest.mark.asyncio
async def test_ensure_enough_messages_accepts_happy_path_without_answering():
    message = FakeMessage()

    result = await ensure_enough_messages(
        message,
        5,
        SummaryRequest(limit_messages=None, time_limit_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS),
    )

    assert result is True
    assert message.answers == []


@pytest.mark.asyncio
async def test_ensure_enough_messages_can_skip_automatic_notification():
    message = FakeMessage()

    result = await ensure_enough_messages(
        message,
        2,
        SummaryRequest(limit_messages=None, time_limit_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS),
        notify=False,
    )

    assert result is False
    assert message.answers == []


@pytest.mark.asyncio
async def test_choose_dynamic_summary_example_returns_selected_example(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    db_engine = object()
    selected_example = SimpleNamespace(summary_log_id=33)
    calls = []

    settings = _presentation("executive_brief", "dry", 0)

    async def fake_fetch_random_good_summary_example(engine, presentation_settings):
        calls.append((engine, presentation_settings))
        return selected_example

    monkeypatch.setattr(
        summary_handlers,
        "fetch_random_good_summary_example",
        fake_fetch_random_good_summary_example,
    )
    monkeypatch.setattr(summary_handlers.config, "SUMMARY_DYNAMIC_EXAMPLES_ENABLED", True)

    example = await choose_dynamic_summary_example(db_engine, chat_id=42, presentation_settings=settings)

    assert example is selected_example
    assert calls == [(db_engine, settings)]


@pytest.mark.asyncio
async def test_choose_dynamic_summary_example_skips_database_when_disabled(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    calls = []

    async def fake_fetch_random_good_summary_example(*args):
        calls.append(args)
        return SimpleNamespace(summary_log_id=33)

    monkeypatch.setattr(
        summary_handlers,
        "fetch_random_good_summary_example",
        fake_fetch_random_good_summary_example,
    )
    monkeypatch.setattr(summary_handlers.config, "SUMMARY_DYNAMIC_EXAMPLES_ENABLED", False)

    example = await choose_dynamic_summary_example(object(), chat_id=42, presentation_settings=_presentation())

    assert example is None
    assert calls == []


@pytest.mark.asyncio
async def test_choose_dynamic_summary_example_handles_missing_db():
    assert await choose_dynamic_summary_example(None, chat_id=42, presentation_settings=_presentation()) is None


@pytest.mark.asyncio
async def test_send_chat_update_reminders_reports_sent_and_failed_chats():
    bot = FakeBot(failing_chat_ids={-1002})
    redis = FakeRedis()
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

    result = await send_chat_update_reminders(bot, chats, redis=redis, cooldown_seconds=3600)

    assert result == ChatReminderResult(sent=1, failed=1, skipped_cooldown=0, skipped_excluded=0)
    assert bot.sent_messages == [(-1001, CHAT_UPDATE_REMINDER_TEXT, {})]
    assert redis.storage == {build_chat_update_reminder_key(-1001): "1"}


@pytest.mark.asyncio
async def test_send_chat_update_reminders_skips_excluded_chats():
    bot = FakeBot()
    chats = [
        KnownBotChat(
            chat_id=-100999,
            chat_type="supergroup",
            title="Excluded Chat",
            username=None,
            is_public=False,
            public_link=None,
            bot_status="seen",
            first_seen_at=None,
            last_seen_at=None,
        ),
    ]

    result = await send_chat_update_reminders(
        bot,
        chats,
        cooldown_seconds=3600,
        excluded_chat_ids=frozenset({-100999}),
    )

    assert result == ChatReminderResult(sent=0, failed=0, skipped_cooldown=0, skipped_excluded=1)
    assert bot.sent_messages == []


@pytest.mark.asyncio
async def test_send_chat_update_reminders_skips_recently_reminded_chats():
    bot = FakeBot()
    redis = FakeRedis()
    redis.storage[build_chat_update_reminder_key(-1001)] = "1"
    chats = [
        KnownBotChat(
            chat_id=-1001,
            chat_type="supergroup",
            title="Recent Chat",
            username=None,
            is_public=False,
            public_link=None,
            bot_status="seen",
            first_seen_at=None,
            last_seen_at=None,
        ),
    ]

    result = await send_chat_update_reminders(
        bot,
        chats,
        redis=redis,
        cooldown_seconds=3600,
        excluded_chat_ids=frozenset(),
    )

    assert result == ChatReminderResult(sent=0, failed=0, skipped_cooldown=1, skipped_excluded=0)
    assert bot.sent_messages == []


@pytest.mark.asyncio
async def test_send_chat_update_reminders_force_bypasses_cooldown():
    bot = FakeBot()
    redis = FakeRedis()
    redis.storage[build_chat_update_reminder_key(-1001)] = "1"
    chats = [
        KnownBotChat(
            chat_id=-1001,
            chat_type="supergroup",
            title="Recent Chat",
            username=None,
            is_public=False,
            public_link=None,
            bot_status="seen",
            first_seen_at=None,
            last_seen_at=None,
        ),
    ]

    result = await send_chat_update_reminders(
        bot,
        chats,
        redis=redis,
        cooldown_seconds=3600,
        excluded_chat_ids=frozenset(),
        force=True,
    )

    assert result == ChatReminderResult(sent=1, failed=0, skipped_cooldown=0, skipped_excluded=0)
    assert bot.sent_messages == [(-1001, CHAT_UPDATE_REMINDER_TEXT, {})]


@pytest.mark.asyncio
async def test_send_prompt_settings_announcements_targets_groups_only():
    bot = FakeBot()
    redis = FakeRedis()
    chats = [
        KnownBotChat(-1001, "supergroup", "Group", None, False, None, "member", None, None),
        KnownBotChat(-1002, "group", "Excluded", None, False, None, "member", None, None),
        KnownBotChat(42, "private", "Private", None, False, None, "seen", None, None),
    ]

    result = await send_prompt_settings_announcements(
        bot,
        chats,
        redis=redis,
        cooldown_seconds=3600,
        excluded_chat_ids=frozenset({-1002}),
    )

    assert result == ChatReminderResult(
        sent=1,
        failed=0,
        skipped_cooldown=0,
        skipped_excluded=1,
        skipped_non_group=1,
    )
    assert bot.sent_messages == [(-1001, PROMPT_SETTINGS_ANNOUNCEMENT_TEXT, {})]
    assert redis.storage == {build_prompt_settings_announcement_key(-1001): "1"}


@pytest.mark.asyncio
async def test_send_prompt_settings_announcements_respects_own_cooldown():
    bot = FakeBot()
    redis = FakeRedis()
    redis.storage[build_prompt_settings_announcement_key(-1001)] = "1"
    chats = [KnownBotChat(-1001, "supergroup", "Group", None, False, None, "member", None, None)]

    result = await send_prompt_settings_announcements(
        bot,
        chats,
        redis=redis,
        cooldown_seconds=3600,
        excluded_chat_ids=frozenset(),
    )

    assert result == ChatReminderResult(sent=0, failed=0, skipped_cooldown=1)
    assert bot.sent_messages == []


@pytest.mark.asyncio
async def test_can_manage_presentation_allows_owner_without_membership_lookup():
    bot = FakeMembershipBot(fail=True)
    chat = SimpleNamespace(id=-1001, type="supergroup")

    allowed = await can_manage_presentation(
        bot,
        chat,
        SimpleNamespace(id=DEBUG_USER_ID),
    )

    assert allowed is True
    assert bot.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["administrator", "creator"])
async def test_can_manage_presentation_allows_group_admin(status):
    bot = FakeMembershipBot(status=status)
    chat = SimpleNamespace(id=-1001, type="supergroup")

    allowed = await can_manage_presentation(bot, chat, SimpleNamespace(id=7))

    assert allowed is True
    assert bot.calls == [(-1001, 7)]


@pytest.mark.asyncio
async def test_can_manage_presentation_rejects_regular_member_and_private_chat():
    bot = FakeMembershipBot(status="member")

    assert await can_manage_presentation(
        bot,
        SimpleNamespace(id=-1001, type="group"),
        SimpleNamespace(id=7),
    ) is False
    assert await can_manage_presentation(
        bot,
        SimpleNamespace(id=42, type="private"),
        SimpleNamespace(id=7),
    ) is False
    assert bot.calls == [(-1001, 7)]


def test_count_recent_logs_counts_valid_recent_rows():
    raw_logs = [
        json.dumps({"ts": 95.0}),
        json.dumps({"ts": 80.0}),
        json.dumps({"ts": 10.0}),
        "not-json",
    ]

    assert count_recent_logs(raw_logs, current_ts=100.0, period_seconds=25) == 2


def test_build_feedback_details_prompt_targets_feedback_author_only():
    user = types.User(id=7, is_bot=False, first_name="Alice")

    prompt = build_feedback_details_prompt(user)

    assert prompt["text"].startswith("Alice, что добавить к оценке?")
    assert prompt["entities"][0].type == "text_mention"
    assert prompt["entities"][0].offset == 0
    assert prompt["entities"][0].length == len("Alice")
    assert prompt["entities"][0].user == user
    assert prompt["reply_markup"].selective is True


@pytest.mark.asyncio
async def test_bad_feedback_saves_without_requesting_details(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    saved_feedback = {}

    async def fake_save_feedback_for_summary(
        db_engine,
        chat_id,
        telegram_message_id,
        user_id,
        feedback_value,
        sentiment,
    ):
        saved_feedback["value"] = (
            db_engine,
            chat_id,
            telegram_message_id,
            user_id,
            feedback_value,
            sentiment,
        )
        return True

    monkeypatch.setattr(feedback_handlers, "save_feedback_for_summary", fake_save_feedback_for_summary)

    redis = FakeRedis()
    services = SimpleNamespace(db_engine=None, redis=redis)
    message = FakeMessage(message_id=100)
    callback = FakeCallback("summary_feedback:bad", message=message)

    await handle_summary_feedback_callback(services, callback)

    assert saved_feedback["value"] == (None, 42, 100, 7, "bad", "negative")
    assert message.answers == []
    assert callback.answers == [("Фидбек сохранен. Комментарий можно добавить кнопкой ниже.", {})]


@pytest.mark.asyncio
async def test_feedback_callback_rate_limits_repeated_clicks(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    saved_calls = []

    async def fake_save_feedback_for_summary(
        db_engine,
        chat_id,
        telegram_message_id,
        user_id,
        feedback_value,
        sentiment,
    ):
        saved_calls.append((chat_id, telegram_message_id, user_id, feedback_value, sentiment))
        return True

    monkeypatch.setattr(feedback_handlers, "save_feedback_for_summary", fake_save_feedback_for_summary)

    redis = FakeRedis()
    services = SimpleNamespace(db_engine=None, redis=redis)
    message = FakeMessage(message_id=100)
    first_callback = FakeCallback("summary_feedback:bad", message=message)
    second_callback = FakeCallback("summary_feedback:good", message=message)

    await handle_summary_feedback_callback(services, first_callback)
    await handle_summary_feedback_callback(services, second_callback)

    assert saved_calls == [(42, 100, 7, "bad", "negative")]
    assert first_callback.answers == [("Фидбек сохранен. Комментарий можно добавить кнопкой ниже.", {})]
    assert second_callback.answers == [("Фидбек уже обрабатывается, подожди пару секунд.", {})]


@pytest.mark.asyncio
async def test_feedback_details_callback_requires_existing_feedback(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    async def fake_has_feedback_for_summary(db_engine, chat_id, telegram_message_id, user_id):
        return False

    monkeypatch.setattr(feedback_handlers, "has_feedback_for_summary", fake_has_feedback_for_summary)

    redis = FakeRedis()
    services = SimpleNamespace(db_engine=None, redis=redis)
    message = FakeMessage(message_id=100)
    callback = FakeCallback("summary_feedback_details:", message=message)

    await handle_summary_feedback_details_callback(services, callback)

    assert redis.storage == {}
    assert message.answers == []
    assert callback.answers == [("Сначала поставь оценку, потом можно добавить комментарий.", {})]


@pytest.mark.asyncio
async def test_feedback_details_callback_starts_prompt_after_existing_feedback(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    scheduled = []

    async def fake_has_feedback_for_summary(db_engine, chat_id, telegram_message_id, user_id):
        return True

    monkeypatch.setattr(feedback_handlers, "has_feedback_for_summary", fake_has_feedback_for_summary)
    monkeypatch.setattr(
        feedback_handlers,
        "schedule_delete_feedback_prompt_on_timeout",
        lambda *args: scheduled.append(args),
    )

    redis = FakeRedis()
    services = SimpleNamespace(db_engine=None, redis=redis)
    message = FakeMessage(message_id=100)
    callback = FakeCallback("summary_feedback_details:", message=message)

    await handle_summary_feedback_details_callback(services, callback)

    assert message.answers[0][0].startswith("Alice, что добавить к оценке?")
    assert "pending_feedback_details:42:7" in redis.storage
    assert callback.answers == [("Ответь на сообщение бота коротким комментарием.", {})]
    assert scheduled == [(services, 42, 7, message.answer_messages[0])]


@pytest.mark.asyncio
async def test_feedback_details_callback_rate_limits_repeated_prompts(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    async def fake_has_feedback_for_summary(db_engine, chat_id, telegram_message_id, user_id):
        return True

    monkeypatch.setattr(feedback_handlers, "has_feedback_for_summary", fake_has_feedback_for_summary)
    monkeypatch.setattr(feedback_handlers, "schedule_delete_feedback_prompt_on_timeout", lambda *args: None)

    redis = FakeRedis()
    services = SimpleNamespace(db_engine=None, redis=redis)
    message = FakeMessage(message_id=100)
    first_callback = FakeCallback("summary_feedback_details:", message=message)
    second_callback = FakeCallback("summary_feedback_details:", message=message)

    await handle_summary_feedback_details_callback(services, first_callback)
    await handle_summary_feedback_details_callback(services, second_callback)

    assert len(message.answers) == 1
    assert first_callback.answers == [("Ответь на сообщение бота коротким комментарием.", {})]
    assert second_callback.answers == [("Комментарий уже запрошен, подожди пару секунд.", {})]


def test_debug_info_and_keyboard_include_manual_delete_button():
    option = LlmModelOption(
        model_id="deepseek:deepseek-v4-flash",
        provider="DeepSeek API",
        label="DeepSeek API · deepseek-v4-flash",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="key",
    )
    services = SimpleNamespace(list_model_options=lambda: (option,))
    active_model = ActiveLlmModel(option=option, client=object())
    presentation = _presentation("anime_recaper", "friendly", 1)

    debug_info = build_debug_info(
        redis_logs_count=10,
        last_day_count=5,
        db_status="✅ Подключена",
        active_model=active_model,
        generation_settings=SummaryGenerationSettings(
            max_output_tokens=2400,
            thinking_mode="enabled",
        ),
        presentation_settings=presentation,
        chunking_enabled=True,
        chunk_stats=ChunkRuntimeStats(active_chunk_size=12, summarized_chunk_count=4, last_status="ready:42-1"),
    )
    keyboard = build_llm_model_settings_keyboard(
        services,
        option.model_id,
        SummaryGenerationSettings(max_output_tokens=2400, thinking_mode="enabled"),
        presentation,
        chunking_enabled=True,
    )

    assert "через 3 минуты" in debug_info
    assert "Output tokens: 2400" in debug_info
    assert "Thinking: думает" in debug_info
    assert "Стиль: Anime recaper" in debug_info
    assert "Тон: Дружелюбный" in debug_info
    assert "Агрессивность: 1 · Легкая" in debug_info
    assert "Chunking: включен" in debug_info
    assert "Чатов с chunking" not in debug_info
    assert "Active chunk size: 12" in debug_info
    assert "\\n" not in debug_info
    assert "\n🔹 Всего логов" in debug_info
    assert keyboard.inline_keyboard[-5][0].callback_data == f"{SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX}auto"
    assert keyboard.inline_keyboard[-4][0].callback_data == (
        f"{SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX}disabled"
    )
    assert keyboard.inline_keyboard[-3][0].callback_data == f"{CHUNKING_SETTINGS_CALLBACK_PREFIX}on"
    assert keyboard.inline_keyboard[-2][0].callback_data == PRESENTATION_OPEN_CALLBACK_DATA
    assert keyboard.inline_keyboard[-1][0].text == "🗑 Удалить сообщение"
    assert keyboard.inline_keyboard[-1][0].callback_data == DEBUG_DELETE_CALLBACK_DATA


def test_presentation_panel_info_and_keyboard_are_compact():
    styles = load_style_catalog()
    tones = load_tone_catalog()
    services = SimpleNamespace(
        list_summary_styles=lambda: styles.options,
        list_summary_tones=lambda: tones.options,
    )
    chat = SimpleNamespace(id=-1001, title="Managed Chat")
    presentation = _presentation("tech_observer", "dry", 0)

    info = build_presentation_panel_info(chat, presentation)
    keyboard = build_presentation_panel_keyboard(presentation)
    style_keyboard = build_presentation_option_keyboard(
        services,
        presentation,
        PRESENTATION_COMPONENT_STYLE,
    )

    assert "Настройка пересказа" in info
    assert "Managed Chat" in info
    assert "Стиль: <b>Tech observer</b>" in info
    assert "Тон: <b>Сухой</b>" in info
    assert "Агрессивность: <b>0 · Спокойная</b>" in info
    assert "Output tokens" not in info
    assert keyboard.inline_keyboard[0][0].callback_data == f"{PRESENTATION_MENU_CALLBACK_PREFIX}s"
    assert keyboard.inline_keyboard[-2][0].callback_data == PRESENTATION_RESET_CALLBACK_DATA
    assert keyboard.inline_keyboard[-1][0].callback_data == PRESENTATION_DELETE_CALLBACK_DATA
    assert style_keyboard.inline_keyboard[0][0].callback_data == f"{PRESENTATION_SET_CALLBACK_PREFIX}s:anime_recaper"


def test_parse_admin_chat_settings_target_accepts_optional_chat_id():
    assert parse_admin_chat_settings_target("/debug_chat_settings") is None
    assert parse_admin_chat_settings_target("/debug_chat_settings -1001") == -1001
    assert parse_admin_chat_settings_target("/debug_chat_settings@sum_bot 42") == 42

    with pytest.raises(ValueError):
        parse_admin_chat_settings_target("/debug_chat_settings nope")


def test_admin_chat_settings_list_keyboard_paginates_known_chats():
    chats = [
        KnownBotChat(
            chat_id=-1000 - index,
            chat_type="supergroup",
            title=f"Chat {index}",
            username=None,
            is_public=False,
            public_link=None,
            bot_status="seen",
            first_seen_at=None,
            last_seen_at=None,
        )
        for index in range(9)
    ]

    text = build_admin_chat_settings_list_text(chats, page=1, chunking_enabled_chats=3)
    keyboard = build_admin_chat_settings_list_keyboard(chats, page=1)

    assert "Страница: 2/2" in text
    assert "Чатов с chunking: 3" in text
    assert "Chat 8" in text
    assert keyboard.inline_keyboard[0][0].callback_data == f"{ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX}-1008"
    assert keyboard.inline_keyboard[-1][0].callback_data == f"{ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX}0"


def test_admin_chat_settings_info_and_keyboard_target_selected_chat():
    available_option = LlmModelOption(
        model_id="deepseek:deepseek-v4-flash",
        provider="DeepSeek API",
        label="DeepSeek API · deepseek-v4-flash",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="key",
    )
    locked_option = LlmModelOption(
        model_id="deepseek:deepseek-v4-pro",
        provider="DeepSeek API",
        label="DeepSeek API · deepseek-v4-pro",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        api_key="",
    )
    style_catalog = load_style_catalog()
    tone_catalog = load_tone_catalog()
    services = SimpleNamespace(
        list_model_options=lambda: (available_option, locked_option),
        list_summary_styles=lambda: style_catalog.options,
        list_summary_tones=lambda: tone_catalog.options,
    )
    active_model = ActiveLlmModel(option=available_option, client=object())
    settings = SummaryGenerationSettings(max_output_tokens=2400, thinking_mode="enabled")
    presentation = _presentation("executive_brief", "dry", 0)
    known_chat = KnownBotChat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Managed Chat",
        username="managed_chat",
        is_public=True,
        public_link="https://t.me/managed_chat",
        bot_status="administrator",
        first_seen_at=None,
        last_seen_at=None,
    )

    info = build_admin_chat_settings_info(
        -1001,
        active_model,
        settings,
        presentation,
        known_chat=known_chat,
        chunking_enabled=True,
        chunking_enabled_chats=3,
        chunk_stats=ChunkRuntimeStats(active_chunk_size=7, summarized_chunk_count=3, last_status="queued:42-1"),
    )
    keyboard = build_admin_chat_settings_keyboard(
        services,
        -1001,
        available_option.model_id,
        settings,
        presentation,
        known_chat=known_chat,
        chunking_enabled=True,
    )
    callback_data = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert "Managed Chat (@managed_chat)" in info
    assert "id: <code>-1001</code>" in info
    assert "Output tokens: 2400" in info
    assert "Стиль: Executive brief" in info
    assert "Тон: Сухой" in info
    assert "Агрессивность: 0 · Спокойная" in info
    assert "Chunking: включен" in info
    assert "Чатов с chunking: 3" in info
    assert keyboard.inline_keyboard[0][0].callback_data == f"{ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX}-1001:0"
    assert keyboard.inline_keyboard[1][0].text.startswith("🔒")
    assert f"{ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX}-1001:auto" in callback_data
    assert f"{ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX}-1001:enabled" in callback_data
    assert f"{ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX}-1001:on" in callback_data
    assert f"{ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX}-1001:off" in callback_data
    assert f"{ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX}-1001:s" in callback_data
    assert f"{ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX}-1001:t" in callback_data
    assert f"{ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX}-1001:a" in callback_data
    assert keyboard.inline_keyboard[-2][0].callback_data == (
        f"{ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX}-1001"
    )
    assert keyboard.inline_keyboard[-1][0].callback_data == ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA
    assert all(
        len(button.callback_data.encode("utf-8")) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_admin_chat_settings_callbacks_fit_telegram_limit_for_supergroup_id():
    style_catalog = load_style_catalog()
    tone_catalog = load_tone_catalog()
    services = SimpleNamespace(
        list_model_options=lambda: (),
        list_summary_styles=lambda: style_catalog.options,
        list_summary_tones=lambda: tone_catalog.options,
    )
    chat_id = -1001234567890
    presentation = _presentation("tech_observer", "dry", 0)
    keyboards = (
        build_admin_chat_settings_keyboard(
            services,
            chat_id,
            "unused",
            SummaryGenerationSettings(),
            presentation,
        ),
        build_admin_presentation_option_keyboard(
            services,
            chat_id,
            presentation,
            PRESENTATION_COMPONENT_STYLE,
        ),
        build_admin_chat_leave_confirmation_keyboard(chat_id),
        build_admin_chat_removed_keyboard(chat_id),
        build_admin_chat_back_keyboard(),
    )

    assert all(
        len(button.callback_data.encode("utf-8")) <= 64
        for keyboard in keyboards
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_admin_chat_settings_left_chat_allows_readd_instead_of_leave():
    services = SimpleNamespace(list_model_options=lambda: ())
    known_chat = KnownBotChat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Removed Chat",
        username=None,
        is_public=False,
        public_link=None,
        bot_status="left",
        first_seen_at=None,
        last_seen_at=None,
    )

    keyboard = build_admin_chat_settings_keyboard(
        services,
        -1001,
        "unused",
        SummaryGenerationSettings(),
        known_chat=known_chat,
        chat_approval_status="left",
    )

    assert keyboard.inline_keyboard[-2][0].callback_data == f"{ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX}-1001"
    assert keyboard.inline_keyboard[-1][0].callback_data == ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA


def test_admin_chat_leave_confirmation_requires_explicit_confirmation():
    known_chat = KnownBotChat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Managed Chat",
        username=None,
        is_public=False,
        public_link=None,
        bot_status="administrator",
        first_seen_at=None,
        last_seen_at=None,
    )

    text = build_admin_chat_leave_confirmation_text(-1001, known_chat)
    keyboard = build_admin_chat_leave_confirmation_keyboard(-1001)

    assert "Подтвердить удаление" in text
    assert keyboard.inline_keyboard[0][0].callback_data == (
        f"{ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX}-1001"
    )
    assert keyboard.inline_keyboard[1][0].callback_data == f"{ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX}-1001"


def test_admin_chat_removed_and_readd_screens_keep_safe_navigation():
    removed_text = build_admin_chat_removed_text(-1001)
    removed_keyboard = build_admin_chat_removed_keyboard(-1001)
    readd_text = build_admin_chat_readd_allowed_text(-1001)
    back_keyboard = build_admin_chat_back_keyboard()

    assert "Повторное добавление заблокировано" in removed_text
    assert removed_keyboard.inline_keyboard[0][0].callback_data == (
        f"{ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX}-1001"
    )
    assert removed_keyboard.inline_keyboard[1][0].callback_data == ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA
    assert "добавить его обратно может любой участник" in readd_text
    assert back_keyboard.inline_keyboard[0][0].callback_data == ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA


def test_parse_admin_chat_setting_callback_returns_target_chat_and_value():
    assert parse_admin_chat_setting_callback(
        f"{ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX}-1001:2400",
        ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX,
    ) == (-1001, "2400")
    assert parse_admin_chat_setting_callback("other:-1001:2400", ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX) is None
    assert parse_admin_chat_setting_callback(ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX, ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX) is None
    assert parse_admin_chat_setting_callback(
        f"{ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX}bad:2400",
        ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX,
    ) is None


def test_build_summary_message_variants_degrades_large_contexts():
    messages = [{"text": str(index)} for index in range(100)]

    variants = build_summary_message_variants(messages)

    assert [len(variant) for variant in variants] == [100, 50, 15]
    assert variants[1] == messages[-50:]
    assert variants[2] == messages[-15:]


@pytest.mark.asyncio
async def test_fetch_messages_for_summary_request_keeps_raw_flow_when_chunking_disabled(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    raw_messages = [{"ts": 100.0, "message_text": "raw"}]

    async def fake_fetch_messages_for_summary(redis, chat_id, limit_messages, time_limit_seconds):
        return raw_messages

    monkeypatch.setattr(summary_handlers, "fetch_messages_for_summary", fake_fetch_messages_for_summary)

    services = SimpleNamespace(redis=object())

    source_bundle = await fetch_messages_for_summary_request(
        services,
        42,
        SummaryRequest(limit_messages=None, time_limit_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS),
        chunking_enabled=False,
    )

    assert source_bundle.raw_messages == raw_messages
    assert source_bundle.chunk_records == []


@pytest.mark.asyncio
async def test_fetch_messages_for_summary_request_loads_chunk_records(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    raw_messages = [{"ts": 300.0, "message_text": "raw"}]
    chunk_records = [SimpleNamespace(source_message_count=50)]

    async def fake_fetch_messages_for_summary(redis, chat_id, limit_messages, time_limit_seconds):
        return raw_messages

    async def fake_fetch_summary_source_bundle(redis, chat_id, **kwargs):
        return SimpleNamespace(raw_messages=raw_messages, chunk_records=chunk_records, total_source_messages=51)

    monkeypatch.setattr(summary_handlers, "fetch_messages_for_summary", fake_fetch_messages_for_summary)
    monkeypatch.setattr(summary_handlers, "fetch_summary_source_bundle", fake_fetch_summary_source_bundle)

    services = SimpleNamespace(redis=object())

    source_bundle = await fetch_messages_for_summary_request(
        services,
        42,
        SummaryRequest(limit_messages=None, time_limit_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS),
        chunking_enabled=True,
    )

    assert source_bundle.raw_messages == raw_messages
    assert source_bundle.chunk_records == chunk_records


@pytest.mark.asyncio
async def test_generate_summary_with_fallbacks_uses_next_model(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    first_model = ActiveLlmModel(
        option=LlmModelOption(
            model_id="deepseek:deepseek-v4-pro",
            provider="DeepSeek API",
            label="DeepSeek API · deepseek-v4-pro",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="key",
        ),
        client=object(),
    )
    second_model = ActiveLlmModel(
        option=LlmModelOption(
            model_id="openrouter:deepseek/deepseek-v4-flash",
            provider="OpenRouter",
            label="OpenRouter · deepseek/deepseek-v4-flash",
            model_name="deepseek/deepseek-v4-flash",
            base_url="https://openrouter.ai/api/v1",
            api_key="key",
        ),
        client=object(),
    )
    calls = []

    async def fake_generate_summary(
        llm_client,
        model_name,
        prepared_context,
        system_prompt,
        chat_id,
        dynamic_example=None,
        provider=None,
        deadline=None,
        max_output_tokens_override=None,
        thinking_mode="disabled",
    ):
        calls.append((model_name, max_output_tokens_override, thinking_mode))
        if model_name == "deepseek-v4-pro":
            return None
        return SummaryResult(
            text="User_1 recovered",
            model_name=model_name,
            input_tokens=10,
            output_tokens=5,
            anonymized_context=prepared_context.rendered_text,
        )

    monkeypatch.setattr(summary_handlers, "generate_summary", fake_generate_summary)

    summary, anon, prepared_context = await generate_summary_with_fallbacks(
        (first_model, second_model),
        [{"author_name": "Alice", "message_text": "hello", "ts": 1.0}],
        "base prompt",
        chat_id=42,
        generation_settings=SummaryGenerationSettings(
            max_output_tokens=2400,
            thinking_mode="enabled",
        ),
    )

    assert calls == [
        ("deepseek-v4-pro", 2400, "enabled"),
        ("deepseek/deepseek-v4-flash", 2400, "enabled"),
    ]
    assert summary is not None
    assert summary.model_name == "deepseek/deepseek-v4-flash"
    assert anon is not None
    assert prepared_context is not None


@pytest.mark.asyncio
async def test_generate_summary_with_fallbacks_stops_when_budget_is_exhausted(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    first_model = ActiveLlmModel(
        option=LlmModelOption(
            model_id="deepseek:deepseek-v4-pro",
            provider="DeepSeek API",
            label="DeepSeek API · deepseek-v4-pro",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="key",
        ),
        client=object(),
    )
    second_model = ActiveLlmModel(
        option=LlmModelOption(
            model_id="openrouter:deepseek/deepseek-v4-flash",
            provider="OpenRouter",
            label="OpenRouter · deepseek/deepseek-v4-flash",
            model_name="deepseek/deepseek-v4-flash",
            base_url="https://openrouter.ai/api/v1",
            api_key="key",
        ),
        client=object(),
    )
    current_time = 100.0
    calls = []

    def fake_monotonic():
        return current_time

    async def fake_generate_summary(
        llm_client,
        model_name,
        prepared_context,
        system_prompt,
        chat_id,
        dynamic_example=None,
        provider=None,
        deadline=None,
        max_output_tokens_override=None,
        thinking_mode="disabled",
    ):
        nonlocal current_time
        calls.append((model_name, deadline))
        current_time = 106.0
        return None

    monkeypatch.setattr(summary_handlers.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(summary_handlers, "SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(summary_handlers, "generate_summary", fake_generate_summary)

    summary, anon, prepared_context = await generate_summary_with_fallbacks(
        (first_model, second_model),
        [{"author_name": "Alice", "message_text": "hello", "ts": 1.0}],
        "base prompt",
        chat_id=42,
    )

    assert calls == [("deepseek-v4-pro", 105.0)]
    assert summary is None
    assert anon is None
    assert prepared_context is None


@pytest.mark.asyncio
async def test_generate_chunk_native_summary_with_fallbacks_uses_next_model(monkeypatch):
    import sumbot.telegram_handlers.summary as summary_handlers

    first_model = ActiveLlmModel(
        option=LlmModelOption(
            model_id="deepseek:deepseek-v4-pro",
            provider="DeepSeek API",
            label="DeepSeek API · deepseek-v4-pro",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="key",
        ),
        client=object(),
    )
    second_model = ActiveLlmModel(
        option=LlmModelOption(
            model_id="openrouter:deepseek/deepseek-v4-flash",
            provider="OpenRouter",
            label="OpenRouter · deepseek/deepseek-v4-flash",
            model_name="deepseek/deepseek-v4-flash",
            base_url="https://openrouter.ai/api/v1",
            api_key="key",
        ),
        client=object(),
    )
    bundle = SummarySourceBundle(
        raw_messages=[
            {"ts": 300.0, "author_name": "Alice", "author_id": 10, "author_username": "alice", "message_text": "raw"}
        ],
        chunk_records=[
            ChunkSummaryRecord(
                chat_id=42,
                chunk_id="42-1",
                message_count=50,
                ts_from=100.0,
                ts_to=200.0,
                participants=(
                    ChunkParticipant("speaker_id_10", "Alice", 10, "alice"),
                ),
                topics=("topic",),
                events=(ChunkEvent("speaker_id_10", "older event"),),
                open_loops=(),
                source_message_count=50,
            )
        ],
    )
    calls = []

    async def fake_generate_summary(
        llm_client,
        model_name,
        prepared_context,
        system_prompt,
        chat_id,
        dynamic_example=None,
        provider=None,
        deadline=None,
        max_output_tokens_override=None,
        thinking_mode="disabled",
    ):
        calls.append((model_name, "precomputed_chunk_summaries" in prepared_context.rendered_text))
        if model_name == "deepseek-v4-pro":
            return None
        return SummaryResult(
            text="User_1 recovered",
            model_name=model_name,
            input_tokens=10,
            output_tokens=5,
            anonymized_context=prepared_context.rendered_text,
        )

    monkeypatch.setattr(summary_handlers, "generate_summary", fake_generate_summary)

    summary, anon, prepared_context = await generate_chunk_native_summary_with_fallbacks(
        (first_model, second_model),
        bundle,
        "base prompt",
        chat_id=42,
        generation_settings=SummaryGenerationSettings(max_output_tokens=2400, thinking_mode="enabled"),
    )

    assert calls == [
        ("deepseek-v4-pro", True),
        ("deepseek/deepseek-v4-flash", True),
    ]
    assert summary is not None
    assert summary.model_name == "deepseek/deepseek-v4-flash"
    assert anon is not None
    assert prepared_context is not None


@pytest.mark.asyncio
async def test_delete_debug_message_pair_deletes_command_and_debug_message():
    class FakeTelegramBot:
        def __init__(self):
            self.deleted = []

        async def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

    class FakeDebugMessage:
        def __init__(self):
            self.chat = SimpleNamespace(id=42)
            self.message_id = 100
            self.bot = FakeTelegramBot()
            self.deleted = False

        async def delete(self):
            self.deleted = True

    redis = FakeRedis()
    debug_message = FakeDebugMessage()
    redis.storage[build_debug_command_message_key(42, 100)] = "99"
    services = SimpleNamespace(redis=redis)

    await delete_debug_message_pair(services, debug_message)

    assert debug_message.bot.deleted == [(42, 99)]
    assert debug_message.deleted is True
    assert build_debug_command_message_key(42, 100) not in redis.storage


@pytest.mark.asyncio
async def test_handle_pending_feedback_details_saves_reply_and_skips_history(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    redis = FakeRedis()
    redis.storage["pending_feedback_details:42:7"] = json.dumps(
        {
            "chat_id": 42,
            "telegram_message_id": 100,
            "user_id": 7,
            "prompt_message_id": 200,
        }
    )
    services = SimpleNamespace(redis=redis, db_engine=object())
    message = FakeMessage(text="  слишком длинно  ", user_id=7, reply_to_message_id=200)
    captured = {}
    scheduled_deletes = []

    async def fake_save_feedback_details_for_summary(
        db_engine,
        chat_id,
        telegram_message_id,
        user_id,
        details,
    ):
        captured["details"] = (db_engine, chat_id, telegram_message_id, user_id, details)
        return True

    monkeypatch.setattr(
        feedback_handlers,
        "save_feedback_details_for_summary",
        fake_save_feedback_details_for_summary,
    )
    monkeypatch.setattr(
        feedback_handlers,
        "schedule_delete_after_delay",
        lambda *messages, delay_seconds: scheduled_deletes.append((messages, delay_seconds)),
    )

    handled = await handle_pending_feedback_details(services, message)

    assert handled is True
    assert captured["details"] == (services.db_engine, 42, 100, 7, "слишком длинно")
    assert "pending_feedback_details:42:7" not in redis.storage
    assert message.answers == [("Спасибо, записал подробности.", {})]
    assert scheduled_deletes == [
        (
            (message, message.answer_messages[0], message.reply_to_message),
            FEEDBACK_DETAILS_DELETE_DELAY_SECONDS,
        )
    ]


@pytest.mark.asyncio
async def test_handle_pending_feedback_details_ignores_non_prompt_reply():
    redis = FakeRedis()
    redis.storage["pending_feedback_details:42:7"] = json.dumps(
        {
            "chat_id": 42,
            "telegram_message_id": 100,
            "user_id": 7,
            "prompt_message_id": 200,
        }
    )
    services = SimpleNamespace(redis=redis, db_engine=object())
    message = FakeMessage(text="обычное сообщение", user_id=7, reply_to_message_id=201)

    handled = await handle_pending_feedback_details(services, message)

    assert handled is False
    assert "pending_feedback_details:42:7" in redis.storage
    assert message.answers == []


@pytest.mark.asyncio
async def test_delete_feedback_prompt_on_timeout_clears_pending_and_deletes_prompt(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    redis = FakeRedis()
    redis.storage["pending_feedback_details:42:7"] = json.dumps(
        {
            "chat_id": 42,
            "telegram_message_id": 100,
            "user_id": 7,
            "prompt_message_id": 200,
        }
    )
    services = SimpleNamespace(redis=redis)
    prompt_message = SimpleNamespace(message_id=200)
    deleted = []

    async def fake_delete_after_delay(*messages, delay_seconds):
        deleted.append((messages, delay_seconds))

    monkeypatch.setattr(feedback_handlers, "delete_after_delay", fake_delete_after_delay)

    await delete_feedback_prompt_on_timeout(
        services,
        chat_id=42,
        user_id=7,
        prompt_message=prompt_message,
        delay_seconds=0,
    )

    assert "pending_feedback_details:42:7" not in redis.storage
    assert deleted == [((prompt_message,), 0)]


@pytest.mark.asyncio
async def test_delete_feedback_prompt_on_timeout_skips_when_user_already_replied(monkeypatch):
    import sumbot.telegram_handlers.feedback_details as feedback_handlers

    redis = FakeRedis()
    services = SimpleNamespace(redis=redis)
    prompt_message = SimpleNamespace(message_id=200)
    deleted = []

    async def fake_delete_after_delay(*messages, delay_seconds):
        deleted.append((messages, delay_seconds))

    monkeypatch.setattr(feedback_handlers, "delete_after_delay", fake_delete_after_delay)

    await delete_feedback_prompt_on_timeout(
        services,
        chat_id=42,
        user_id=7,
        prompt_message=prompt_message,
        delay_seconds=0,
    )

    assert deleted == []
