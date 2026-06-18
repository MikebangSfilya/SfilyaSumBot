import io

from PIL import Image

from tools.analytics.analytics_visualization import HEIGHT, WIDTH, render_analytics_dashboard


def test_render_analytics_dashboard_returns_telegram_ready_png():
    report = {
        "metadata": {
            "period": "30d",
            "timezone": "Europe/Moscow",
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
        },
        "models": [
            {
                "key": "deepseek/deepseek-v4-flash",
                "summaries": 159,
                "feedback": 150,
                "feedback_coverage_pct": 56.6,
                "positive_pct": 54.32,
                "negative_pct": 19.14,
            }
        ],
        "days": [
            {"key": "2026-06-09", "summaries": 8, "negative_pct": 10.0},
            {"key": "2026-06-10", "summaries": 16, "negative_pct": 30.0},
        ],
        "hours": [
            {"key": "23:00", "feedback": 10, "negative_pct": 40.0},
            {"key": "14:00", "feedback": 20, "negative_pct": 19.05},
        ],
    }

    payload = render_analytics_dashboard(report)
    image = Image.open(io.BytesIO(payload))

    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.size == (WIDTH, HEIGHT)
    assert image.mode == "RGB"
