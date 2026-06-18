import json
from datetime import datetime, timezone
from types import SimpleNamespace

from tools.analytics.analytics_export import (
    _serialize_row,
    build_export_caption,
    build_export_payloads,
)


def test_serialize_row_converts_datetime_fields_to_iso_strings():
    created_at = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)
    row = SimpleNamespace(_mapping={"id": 1, "created_at": created_at})

    assert _serialize_row(row, ("created_at",)) == {
        "id": 1,
        "created_at": "2024-05-01T12:30:00+00:00",
    }


def test_build_export_payloads_returns_utf8_json_documents():
    payloads = build_export_payloads(
        summary_rows=[{"id": 1, "summary": "ok"}],
        feedback_rows=[{"id": 2, "sentiment": "positive"}],
        bot_chat_rows=[{"chat_id": 42}],
    )

    assert set(payloads) == {
        "summary_dataset.json",
        "summary_feedback_dataset.json",
        "bot_chats_dataset.json",
    }
    assert json.loads(payloads["summary_dataset.json"].decode("utf-8")) == [
        {"id": 1, "summary": "ok"}
    ]
    assert json.loads(payloads["summary_feedback_dataset.json"].decode("utf-8")) == [
        {"id": 2, "sentiment": "positive"}
    ]
    assert json.loads(payloads["bot_chats_dataset.json"].decode("utf-8")) == [
        {"chat_id": 42}
    ]


def test_build_export_payloads_can_return_single_dataset():
    summary_payloads = build_export_payloads(
        summary_rows=[{"id": 1}],
        feedback_rows=[{"id": 2}],
        dataset="summary",
    )
    feedback_payloads = build_export_payloads(
        summary_rows=[{"id": 1}],
        feedback_rows=[{"id": 2}],
        dataset="feedback",
    )

    assert set(summary_payloads) == {"summary_dataset.json"}
    assert set(feedback_payloads) == {"summary_feedback_dataset.json"}

    chat_payloads = build_export_payloads(
        summary_rows=[{"id": 1}],
        feedback_rows=[{"id": 2}],
        bot_chat_rows=[{"chat_id": 42}],
        dataset="chats",
    )

    assert set(chat_payloads) == {"bot_chats_dataset.json"}


def test_build_export_caption_includes_dataset_counts():
    caption = build_export_caption(
        [{"id": 1}],
        [{"id": 2}, {"id": 3}],
        [{"chat_id": 42}],
        total_summary_logs=432,
    )

    assert "dataset: all" in caption
    assert "summary_logs: 1" in caption
    assert "summary_logs_total: 432" in caption
    assert "summary_feedback: 2" in caption
    assert "bot_chats: 1" in caption
    assert "generated_at:" in caption


def test_build_export_caption_includes_summary_log_limit():
    caption = build_export_caption(
        [{"id": 1}],
        [{"id": 2}],
        total_summary_logs=432,
        summary_log_limit=50,
    )

    assert "summary_logs_limit: 50" in caption
    assert "summary_logs: 1" in caption
    assert "summary_logs_total: 432" in caption


def test_build_export_caption_includes_summary_log_limit_for_feedback_dataset():
    caption = build_export_caption(
        [{"id": 1}],
        [{"id": 2}],
        dataset="feedback",
        summary_log_limit=50,
    )

    assert "dataset: feedback" in caption
    assert "summary_logs_limit: 50" in caption
    assert "summary_logs:" not in caption
    assert "summary_feedback: 1" in caption


def test_build_export_caption_rejects_invalid_summary_log_limit():
    try:
        build_export_caption([], [], summary_log_limit=0)
    except ValueError as exc:
        assert "summary_log_limit" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_build_export_caption_includes_only_selected_dataset_count():
    caption = build_export_caption([{"id": 1}], [{"id": 2}, {"id": 3}], dataset="feedback")

    assert "dataset: feedback" in caption
    assert "summary_logs:" not in caption
    assert "summary_feedback: 2" in caption


def test_build_export_caption_includes_only_chats_dataset_count():
    caption = build_export_caption(
        [{"id": 1}],
        [{"id": 2}],
        [{"chat_id": 42}, {"chat_id": 43}],
        dataset="chats",
    )

    assert "dataset: chats" in caption
    assert "summary_logs:" not in caption
    assert "summary_feedback:" not in caption
    assert "bot_chats: 2" in caption
