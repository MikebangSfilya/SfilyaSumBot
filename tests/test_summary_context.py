from anonymizer import Anonymizer
from sumbot.summary_context import (
    PreparedSummaryContext,
    SummaryMessage,
    build_role_tags,
    merge_summary_messages,
    normalize_summary_messages,
    prepare_summary_context,
    sanitize_summary_message_text,
)


def test_normalize_summary_messages_supports_structured_and_legacy_rows():
    messages = normalize_summary_messages(
        [
            {
                "ts": 1.0,
                "message_text": "hello",
                "author_id": 10,
                "author_name": "Alice",
                "author_username": "alice",
                "reply_to_user_id": None,
                "reply_to_username": None,
                "reply_to_name": None,
                "message_id": 10,
            },
            {
                "ts": 2.0,
                "text": "[01.01 10:00] Bob (@bob) (в ответ Alice): hi",
            },
        ]
    )

    assert messages == [
        SummaryMessage(
            ts=1.0,
            author_name="Alice",
            author_id=10,
            author_username="alice",
            reply_to_user_id=None,
            reply_to_username=None,
            reply_to_name=None,
            message_text="hello",
            message_id=10,
        ),
        SummaryMessage(
            ts=2.0,
            author_name="Bob",
            author_username="bob",
            reply_to_name="Alice",
            message_text="hi",
            message_id=None,
        ),
    ]


def test_merge_summary_messages_safely_merges_only_short_consecutive_messages():
    merged = merge_summary_messages(
        [
            SummaryMessage(ts=1.0, author_name="Alice", message_text="one"),
            SummaryMessage(ts=10.0, author_name="Alice", message_text="two"),
            SummaryMessage(ts=20.0, author_name="Alice", message_text="x" * 81),
            SummaryMessage(ts=30.0, author_name="Alice", message_text="reply", reply_to_name="Bob"),
            SummaryMessage(ts=40.0, author_name="Bob", message_text="other"),
        ]
    )

    assert [message.message_text for message in merged] == [
        "one / two",
        "x" * 81,
        "reply",
        "other",
    ]


def test_merge_summary_messages_keeps_same_first_name_with_different_identity_separate():
    merged = merge_summary_messages(
        [
            SummaryMessage(
                ts=1.0,
                author_name="Alex",
                author_id=10,
                author_username="alex_one",
                message_text="я за Genshin",
            ),
            SummaryMessage(
                ts=2.0,
                author_name="Alex",
                author_id=20,
                author_username="alex_two",
                message_text="а я против Genshin",
            ),
        ]
    )

    assert [message.message_text for message in merged] == ["я за Genshin", "а я против Genshin"]


def test_build_role_tags_uses_priority_order():
    tags = build_role_tags(
        [
            SummaryMessage(ts=1.0, author_name="Alice", message_text="надо чинить"),
            SummaryMessage(ts=2.0, author_name="Bob", message_text="ок", reply_to_name="Alice"),
            SummaryMessage(ts=3.0, author_name="Carol", message_text="почему?"),
            SummaryMessage(ts=4.0, author_name="Dan", message_text="лол"),
            SummaryMessage(
                ts=5.0,
                author_name="Eve",
                message_text="развернутая мысль без ключевых слов и без короткой реакции",
            ),
        ]
    )

    assert tags == {
        "name:Alice": "советует",
        "name:Bob": "отвечает",
        "name:Carol": "уточняет",
        "name:Dan": "реагирует",
        "name:Eve": "комментирует",
    }


def test_prepare_summary_context_v2_renders_role_tags_and_counts_merges():
    anonymizer = Anonymizer()

    prepared = prepare_summary_context(
        [
            {"ts": 1.0, "message_text": "сервер умер", "author_name": "Alice"},
            {"ts": 20.0, "message_text": "надо рестартнуть Redis", "author_name": "Alice"},
            {"ts": 40.0, "message_text": "ок?", "author_name": "Bob"},
        ],
        anonymizer,
        enable_v2=True,
    )

    assert prepared == PreparedSummaryContext(
        rendered_text=(
            "User_1 [советует]: сервер умер / надо рестартнуть Redis\n"
            "User_2 [уточняет]: ок?"
        ),
        raw_message_count=3,
        turn_count=2,
        merged_count=1,
    )


def test_prepare_summary_context_legacy_mode_keeps_old_style_without_role_tags():
    anonymizer = Anonymizer()

    prepared = prepare_summary_context(
        [
            {"ts": 1.0, "message_text": "one", "author_name": "Alice"},
            {"ts": 20.0, "message_text": "two", "author_name": "Alice"},
            {"ts": 40.0, "message_text": "other", "author_name": "Bob"},
        ],
        anonymizer,
        enable_v2=False,
    )

    assert prepared.rendered_text == "User_1: one two\nUser_2: other"
    assert prepared.turn_count == 2
    assert prepared.merged_count == 1


def test_sanitize_summary_message_text_replaces_radical_ideology_with_filtered():
    sanitized = sanitize_summary_message_text("фашизм, Гитлер и нацики")

    assert sanitized == "[FILTERED]"


def test_sanitize_summary_message_text_replaces_svo_topic_with_filtered():
    sanitized = sanitize_summary_message_text("СВО, svo, спецоперация и специальная военная операция")

    assert sanitized == "[FILTERED]"


def test_sanitize_summary_message_text_keeps_political_topics_for_neutral_summary():
    sanitized = sanitize_summary_message_text("Путин, бомба, Тяньаньмэнь, ВСУ, война и армия")

    assert sanitized == "Путин, бомба, Тяньаньмэнь, ВСУ, война и армия"


def test_sanitize_summary_message_text_keeps_obscene_chat_language():
    sanitized = sanitize_summary_message_text("Порно и письки сиськи, хуйня, ебать")

    assert sanitized == "Порно и письки сиськи, хуйня, ебать"


def test_prepare_summary_context_filters_only_forbidden_fragments_before_llm():
    anonymizer = Anonymizer()

    prepared = prepare_summary_context(
        [
            {"ts": 1.0, "message_text": "Порно и письки сиськи", "author_name": "Alice"},
            {"ts": 2.0, "message_text": "Путин и Тяньаньмэнь", "author_name": "Bob"},
            {"ts": 3.0, "message_text": "фашизм и нацики", "author_name": "Carol"},
            {"ts": 4.0, "message_text": "СВО и спецоперация", "author_name": "Dan"},
        ],
        anonymizer,
        enable_v2=True,
    )

    assert prepared.rendered_text == (
        "User_1 [реагирует]: Порно и письки сиськи\n"
        "User_2 [реагирует]: Путин и Тяньаньмэнь\n"
        "User_3 [реагирует]: [FILTERED]\n"
        "User_4 [реагирует]: [FILTERED]"
    )
