from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import BigInteger, DateTime, String

from tools.analytics.analytics_report import (
    FEEDBACK_REPORT_QUERY,
    SUMMARY_REPORT_QUERY,
    build_analytics_report,
    parse_period,
    percentile,
    render_csv_report,
    render_text_report,
)


def _summary(
    summary_id: int,
    model: str,
    hour: int,
    *,
    chat_id: int = 100,
    chat_title: str | None = None,
    summary_duration: float | None = None,
    llm_duration: float | None = None,
    style_id: str | None = "classic_chat_storyteller",
    tone_id: str | None = "ironic",
    aggressiveness: int | None = 2,
    trigger_source: str = "manual",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict:
    return {
        "id": summary_id,
        "chat_id": chat_id,
        "chat_title": chat_title or f"Chat {chat_id}",
        "model_name": model,
        "style_id": style_id,
        "tone_id": tone_id,
        "aggressiveness": aggressiveness,
        "trigger_source": trigger_source,
        "input_tokens": input_tokens if input_tokens is not None else 100 * summary_id,
        "output_tokens": output_tokens if output_tokens is not None else 10 * summary_id,
        "summary_duration_seconds": summary_duration,
        "llm_duration_seconds": llm_duration,
        "created_at": datetime(2026, 6, 10, hour, tzinfo=timezone.utc),
    }


def _feedback(
    summary_id: int,
    sentiment: str,
    *,
    chat_id: int = 100,
    chat_title: str | None = None,
    details: str | None = None,
) -> dict:
    return {
        "summary_log_id": summary_id,
        "chat_id": chat_id,
        "chat_title": chat_title or f"Chat {chat_id}",
        "feedback_value": {"positive": "good", "neutral": "neutral", "negative": "bad"}[sentiment],
        "sentiment": sentiment,
        "details": details,
        "created_at": datetime(2026, 6, 10, 20, summary_id, tzinfo=timezone.utc),
        "model_name": "model-a" if summary_id < 3 else "model-b",
        "style_id": "classic_chat_storyteller",
        "tone_id": "ironic",
        "aggressiveness": 2,
        "trigger_source": "manual",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("4w", timedelta(weeks=4)),
        ("all", None),
    ],
)
def test_parse_period(value, expected):
    assert parse_period(value) == expected


def test_parse_period_rejects_invalid_value():
    with pytest.raises(ValueError):
        parse_period("30days")


def test_percentile_interpolates_values():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([1, 2, 3, 4], 0.95) == pytest.approx(3.85)
    assert percentile([], 0.95) is None


@pytest.mark.parametrize("query", [SUMMARY_REPORT_QUERY, FEEDBACK_REPORT_QUERY])
def test_report_queries_type_nullable_filters_for_asyncpg(query):
    assert isinstance(query._bindparams["since"].type, DateTime)
    assert isinstance(query._bindparams["model"].type, String)
    assert isinstance(query._bindparams["chat_id"].type, BigInteger)
    assert "LEFT JOIN bot_chats" in str(query)


def test_build_analytics_report_calculates_quality_latency_and_dimensions():
    summaries = [
        _summary(1, "model-a", 8, summary_duration=5, llm_duration=3),
        _summary(2, "model-a", 9, summary_duration=7, llm_duration=4),
        _summary(
            3,
            "model-b",
            20,
            chat_id=200,
            chat_title="Project chat",
            summary_duration=11,
            llm_duration=9,
            trigger_source="daily_digest",
        ),
        _summary(4, "model-b", 21, chat_id=200, chat_title="Project chat", trigger_source="daily_digest"),
    ]
    feedback = [
        _feedback(1, "positive"),
        _feedback(1, "positive"),
        _feedback(2, "negative", details="Too verbose"),
        _feedback(3, "neutral", chat_id=200, chat_title="Project chat"),
    ]

    report = build_analytics_report(
        summaries,
        feedback,
        period="30d",
        report_timezone=ZoneInfo("Europe/Moscow"),
        generated_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )

    overall = report["overall"]
    assert overall["summaries"] == 4
    assert overall["rated_summaries"] == 3
    assert overall["feedback_coverage_pct"] == 75.0
    assert overall["positive"] == 2
    assert overall["neutral"] == 1
    assert overall["negative"] == 1
    assert overall["positive_pct"] == 50.0
    assert overall["avg_summary_duration_seconds"] == pytest.approx(7.67)
    assert overall["p50_llm_duration_seconds"] == 4.0
    assert overall["p95_llm_duration_seconds"] == pytest.approx(8.5)
    assert overall["gemma_baseline_cost_usd"] == pytest.approx(0.000093)
    assert overall["confidence"] == "ok"

    models = {row["key"]: row for row in report["models"]}
    assert models["model-a"]["feedback_coverage_pct"] == 100.0
    assert models["model-a"]["negative_pct"] == pytest.approx(33.33)
    assert models["model-a"]["confidence"] == "ok"
    assert models["model-b"]["feedback_coverage_pct"] == 50.0
    assert models["model-b"]["confidence"] == "low"

    hours = {row["key"]: row for row in report["hours"]}
    assert set(hours) == {"11:00", "12:00", "23:00", "00:00"}
    assert report["negative_details"] == [
        {
            "created_at": "2026-06-10T23:02:00+03:00",
            "chat": "Chat 100",
            "model": "model-a",
            "source": "manual",
            "category": "length",
            "details": "Too verbose",
        }
    ]

    # Verify negative categories stats
    neg_cats = {row["category"]: row for row in report["negative_categories"]}
    assert neg_cats["length"]["count"] == 1
    assert neg_cats["length"]["pct"] == 100.0
    assert "Negative feedback categories" in render_text_report(report)
    assert "sample_size=1" in render_text_report(report)
    assert "category=length count=1 pct=100.0%" in render_text_report(report)

    model_outlier = next(row for row in report["outliers"] if row["dimension"] == "models")
    assert model_outlier["key"] == "model-a"
    assert model_outlier["negative"] == 1
    assert model_outlier["negative_pct"] == pytest.approx(33.33)
    assert model_outlier["confidence"] == "ok"
    assert report["failure_signals"]["chunk_worker"]["available"] is False

    chats = {row["key"]: row for row in report["chats"]}
    assert chats["Chat 100"]["summaries"] == 2
    assert chats["Project chat"]["summaries"] == 2
    assert report["styles"][0]["key"] == "classic_chat_storyteller"
    assert report["tones"][0]["key"] == "ironic"
    assert report["aggressiveness"][0]["key"] == "2"
    sources = {row["key"]: row for row in report["sources"]}
    assert sources["manual"]["summaries"] == 2
    assert sources["daily_digest"]["summaries"] == 2


def test_analytics_report_estimates_short_log_cost_against_gemma_baseline():
    report = build_analytics_report(
        [
            _summary(
                1,
                "qwen/qwen-2.5-7b-instruct",
                8,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
            _summary(
                2,
                "google/gemma-4-26b-a4b-it",
                9,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
        ],
        [],
        period="all",
        report_timezone=ZoneInfo("UTC"),
    )

    models = {row["key"]: row for row in report["models"]}
    assert models["qwen/qwen-2.5-7b-instruct"]["estimated_cost_usd"] == 0.14
    assert models["qwen/qwen-2.5-7b-instruct"]["gemma_baseline_cost_usd"] == 0.39
    assert models["qwen/qwen-2.5-7b-instruct"]["gemma_savings_usd"] == 0.25
    assert report["overall"]["estimated_cost_usd"] == 0.53
    assert report["overall"]["gemma_baseline_cost_usd"] == 0.78
    assert "cost_estimate_usd=actual 0.53 / gemma_baseline 0.78 / savings 0.25" in render_text_report(report)


def test_analytics_report_marks_historical_presentation_as_unknown():
    report = build_analytics_report(
        [_summary(1, "model-a", 8, style_id=None, tone_id=None, aggressiveness=None)],
        [],
        period="all",
        report_timezone=ZoneInfo("UTC"),
    )

    assert report["styles"][0]["key"] == "legacy/unknown"
    assert report["tones"][0]["key"] == "legacy/unknown"
    assert report["aggressiveness"][0]["key"] == "legacy/unknown"


def test_chat_dimension_keeps_same_titles_separate_without_exposing_ids():
    report = build_analytics_report(
        [
            _summary(1, "model-a", 8, chat_id=100, chat_title="Discussion"),
            _summary(2, "model-a", 9, chat_id=200, chat_title="Discussion"),
        ],
        [_feedback(1, "positive", chat_id=100, chat_title="Discussion")],
        period="7d",
        report_timezone=ZoneInfo("UTC"),
    )

    assert len(report["chats"]) == 2
    assert [row["key"] for row in report["chats"]] == ["Discussion", "Discussion"]
    assert sorted(row["feedback"] for row in report["chats"]) == [0, 1]
    assert "chat=100" not in render_text_report(report)


def test_filtered_report_uses_chat_title_in_metadata():
    report = build_analytics_report(
        [_summary(1, "model-a", 8, chat_id=100, chat_title="Main room")],
        [],
        period="7d",
        report_timezone=ZoneInfo("UTC"),
        chat_id=100,
    )

    assert report["metadata"]["chat_filter"] == "Main room"
    assert "chat=Main room" in render_text_report(report)


def test_report_without_feedback_uses_none_for_undefined_rates():
    report = build_analytics_report(
        [_summary(1, "model-a", 8)],
        [],
        period="7d",
        report_timezone=ZoneInfo("UTC"),
    )

    assert report["overall"]["feedback_coverage_pct"] == 0.0
    assert report["overall"]["positive_pct"] is None
    assert report["overall"]["confidence"] == "low"
    assert "positive=0 (n/a)" in render_text_report(report)
    assert "Failure signals" in render_text_report(report)
    assert "chunk_worker=unavailable" in render_text_report(report)


def test_csv_report_contains_all_dimension_sections():
    report = build_analytics_report(
        [_summary(1, "model-a", 8, summary_duration=5, llm_duration=3)],
        [_feedback(1, "negative", details="Missed topic")],
        period="24h",
        report_timezone=ZoneInfo("UTC"),
    )

    rendered = render_csv_report(report)

    assert "overall,all" in rendered
    assert "models,model-a" in rendered
    assert "styles,classic_chat_storyteller" in rendered
    assert "tones,ironic" in rendered
    assert "aggressiveness,2" in rendered
    assert "sources,manual" in rendered
    assert "failure_signals,chunk_worker" in rendered
    assert "outliers" in rendered
    assert "negative_details" in rendered
    assert "negative_categories,factual" in rendered
    assert "Missed topic" in rendered
