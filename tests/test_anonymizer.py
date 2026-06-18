from anonymizer import Anonymizer, clean_text_for_llm
from sumbot.summary_context import SummaryMessage


def test_anonymizer_masks_pii_and_decodes_names_without_aggregation():
    anonymizer = Anonymizer()
    session_items = [
        {
            "ts": 1.0,
            "text": (
                "[01.01 10:00] Alice (@alice): "
                "contact alice@example.com +7 999 123 45 67 https://example.com @bob"
            ),
        },
        {
            "ts": 2.0,
            "text": "[01.01 10:00] Alice (@alice): second message",
        },
        {
            "ts": 120.0,
            "text": "[01.01 10:02] Bob: separate thought",
        },
    ]

    result = anonymizer.clean_text_for_llm(session_items)

    assert result.splitlines() == [
        "User_1: contact [EMAIL] [PHONE] [URL] @mention_1",
        "User_1: second message",
        "User_2: separate thought",
    ]
    assert anonymizer.decode("User_1 and User_2") == "Alice and Bob"
    assert anonymizer.get_mapping_stats()["total_users"] == 2


def test_clean_text_for_llm_wrapper_uses_given_anonymizer():
    anonymizer = Anonymizer()
    session_items = [{"ts": 1.0, "text": "[01.01 10:00] Alice: hello"}]

    assert clean_text_for_llm(session_items, anonymizer) == "User_1: hello"


def test_render_messages_for_llm_adds_role_tags_for_structured_messages():
    anonymizer = Anonymizer()
    messages = [
        SummaryMessage(ts=1.0, author_name="Alice", message_text="hello"),
        SummaryMessage(ts=2.0, author_name="Bob", message_text="try https://example.com"),
    ]

    rendered = anonymizer.render_messages_for_llm(
        messages,
        role_tags={"Alice": "инициатор", "Bob": "советует"},
    )

    assert rendered.splitlines() == [
        "User_1 [инициатор]: hello",
        "User_2 [советует]: try [URL]",
    ]


def test_render_messages_for_llm_preserves_reply_target_alias():
    anonymizer = Anonymizer()
    messages = [
        SummaryMessage(ts=1.0, author_name="Alice", author_id=10, message_text="source"),
        SummaryMessage(
            ts=2.0,
            author_name="Bob",
            author_id=20,
            reply_to_name="Alice",
            reply_to_user_id=10,
            message_text="answer",
        ),
    ]

    rendered = anonymizer.render_messages_for_llm(messages, role_tags={"id:20": "отвечает"})

    assert rendered.splitlines() == [
        "User_1: source",
        "User_2 [отвечает User_1]: answer",
    ]


def test_render_messages_for_llm_maps_known_and_unknown_mentions_distinctly():
    anonymizer = Anonymizer()
    messages = [
        SummaryMessage(ts=1.0, author_name="Bob", author_id=20, author_username="bob", message_text="topic"),
        SummaryMessage(
            ts=2.0,
            author_name="Alice",
            author_id=10,
            author_username="alice",
            message_text="@bob проверь, @ghost тоже видел, @other нет",
        ),
    ]

    rendered = anonymizer.render_messages_for_llm(messages)

    assert rendered.splitlines() == [
        "User_1: topic",
        "User_2: @User_1 проверь, @mention_1 тоже видел, @mention_2 нет",
    ]
