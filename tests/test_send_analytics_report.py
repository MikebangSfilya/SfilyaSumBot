from datetime import datetime, timezone

import pytest

import tools.analytics.send_analytics_report as report_sender
from tools.analytics.send_analytics_report import (
    build_dashboard_filename,
    build_report_filename,
    build_telegram_report_summary,
)


def _report() -> dict:
    return {
        "metadata": {
            "period": "30d",
            "model_filter": None,
            "chat_filter": None,
        },
        "overall": {
            "summaries": 200,
            "rated_summaries": 112,
            "feedback": 191,
            "feedback_coverage_pct": 56.0,
            "positive": 100,
            "neutral": 50,
            "negative": 41,
            "positive_pct": 52.36,
            "neutral_pct": 26.18,
            "negative_pct": 21.47,
            "p95_llm_duration_seconds": None,
            "p95_summary_duration_seconds": 8.5,
        },
    }


def test_build_telegram_report_summary_contains_key_metrics():
    summary = build_telegram_report_summary(_report())

    assert "SumBot analytics: 30d" in summary
    assert "Feedback coverage: 56.0%" in summary
    assert "Positive: 100 (52.36%)" in summary
    assert "negative: 41 (21.47%)" in summary
    assert "LLM latency p95: n/a" in summary
    assert len(summary) < 4096


@pytest.mark.parametrize(
    ("report_format", "expected"),
    [
        ("text", "sumbot_analytics_30d.txt"),
        ("json", "sumbot_analytics_30d.json"),
        ("csv", "sumbot_analytics_30d.csv"),
    ],
)
def test_build_report_filename_uses_expected_extension(report_format, expected):
    assert build_report_filename("30d", report_format) == expected


def test_build_dashboard_filename_uses_png_extension():
    assert build_dashboard_filename("30d") == "sumbot_analytics_30d.png"


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeBot:
    instances = []

    def __init__(self, token: str):
        self.token = token
        self.session = FakeSession()
        self.messages = []
        self.photos = []
        self.documents = []
        self.instances.append(self)

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def send_document(self, chat_id, document):
        self.documents.append((chat_id, document))

    async def send_photo(self, chat_id, photo):
        self.photos.append((chat_id, photo))


@pytest.mark.asyncio
async def test_send_analytics_report_sends_summary_and_document(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test")
    monkeypatch.setenv("TG_TOKEN", "token")
    monkeypatch.setenv("ANALYTICS_CHAT_ID", "123")
    FakeBot.instances.clear()

    async def fake_fetch_report_rows(db_url, since, model=None, chat_id=None):
        assert db_url == "postgresql+asyncpg://test"
        assert since is not None
        return [], []

    monkeypatch.setattr(report_sender, "fetch_report_rows", fake_fetch_report_rows)
    monkeypatch.setattr(report_sender, "build_analytics_report", lambda *args, **kwargs: _report())
    monkeypatch.setattr(report_sender, "serialize_report", lambda report, report_format: "full report")
    monkeypatch.setattr(report_sender, "render_analytics_dashboard", lambda report: b"png dashboard")

    result = await report_sender.send_analytics_report(bot_factory=FakeBot)

    assert result == 0
    bot = FakeBot.instances[0]
    assert bot.messages[0][0] == 123
    assert "Feedback coverage" in bot.messages[0][1]
    assert bot.photos[0][0] == 123
    assert bot.photos[0][1].filename == "sumbot_analytics_30d.png"
    assert bot.photos[0][1].data == b"png dashboard"
    assert bot.documents[0][0] == 123
    assert bot.documents[0][1].filename == "sumbot_analytics_30d.txt"
    assert bot.documents[0][1].data == b"full report"
    assert bot.session.closed is True
