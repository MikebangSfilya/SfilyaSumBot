import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

from tools.analytics.analytics_report import (
    REPORT_FORMATS,
    build_analytics_report,
    fetch_report_rows,
    parse_period,
    serialize_report,
)
from tools.analytics.analytics_visualization import render_analytics_dashboard


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("send_analytics_report")

REPORT_EXTENSIONS = {"text": "txt", "json": "json", "csv": "csv"}


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _parse_chat_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError("ANALYTICS_CHAT_ID must be a numeric Telegram chat id") from exc


def build_telegram_report_summary(report: dict) -> str:
    metadata = report["metadata"]
    overall = report["overall"]
    return "\n".join(
        (
            f"SumBot analytics: {metadata['period']}",
            f"Summary: {overall['summaries']}",
            (
                f"Feedback coverage: {_display(overall['feedback_coverage_pct'], '%')} "
                f"({overall['rated_summaries']} rated)"
            ),
            (
                f"Positive: {overall['positive']} ({_display(overall['positive_pct'], '%')}), "
                f"neutral: {overall['neutral']} ({_display(overall['neutral_pct'], '%')}), "
                f"negative: {overall['negative']} ({_display(overall['negative_pct'], '%')})"
            ),
            (
                f"LLM latency p95: {_display(overall['p95_llm_duration_seconds'], 's')}; "
                f"summary latency p95: {_display(overall['p95_summary_duration_seconds'], 's')}"
            ),
            f"Model: {metadata['model_filter'] or 'all'}; chat: {metadata['chat_filter'] or 'all'}",
            "Full report is attached.",
        )
    )


def build_report_filename(period: str, report_format: str) -> str:
    safe_period = re.sub(r"[^a-zA-Z0-9_-]+", "_", period).strip("_") or "report"
    return f"sumbot_analytics_{safe_period}.{REPORT_EXTENSIONS[report_format]}"


def build_dashboard_filename(period: str) -> str:
    safe_period = re.sub(r"[^a-zA-Z0-9_-]+", "_", period).strip("_") or "report"
    return f"sumbot_analytics_{safe_period}.png"


async def send_analytics_report(
    *,
    period: str = "30d",
    report_format: str = "text",
    model: str | None = None,
    chat_id: int | None = None,
    timezone_name: str = "Europe/Moscow",
    negative_details_limit: int = 10,
    bot_factory: Callable[..., Bot] = Bot,
) -> int:
    db_url = _require_env("DATABASE_URL")
    tg_token = _require_env("TG_TOKEN")
    analytics_chat_id = _parse_chat_id(_require_env("ANALYTICS_CHAT_ID"))
    if negative_details_limit < 0:
        raise ValueError("negative_details_limit must be zero or greater")

    period_delta = parse_period(period)
    generated_at = datetime.now(timezone.utc)
    since = generated_at - period_delta if period_delta else None
    try:
        report_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc

    summary_rows, feedback_rows = await fetch_report_rows(
        db_url,
        since,
        model=model,
        chat_id=chat_id,
    )
    report = build_analytics_report(
        summary_rows,
        feedback_rows,
        period=period,
        report_timezone=report_timezone,
        generated_at=generated_at,
        since=since,
        model=model,
        chat_id=chat_id,
        negative_details_limit=negative_details_limit,
    )
    rendered = serialize_report(report, report_format)
    filename = build_report_filename(period, report_format)
    dashboard = render_analytics_dashboard(report)

    bot = bot_factory(token=tg_token)
    try:
        await bot.send_message(analytics_chat_id, build_telegram_report_summary(report))
        await bot.send_photo(
            chat_id=analytics_chat_id,
            photo=BufferedInputFile(dashboard, filename=build_dashboard_filename(period)),
        )
        await bot.send_document(
            chat_id=analytics_chat_id,
            document=BufferedInputFile(rendered.encode("utf-8"), filename=filename),
        )
        logger.info(
            "Analytics report sent successfully "
            "(chat_id=%s, period=%s, format=%s, summaries=%s, feedback=%s)",
            analytics_chat_id,
            period,
            report_format,
            report["overall"]["summaries"],
            report["overall"]["feedback"],
        )
        return 0
    finally:
        await bot.session.close()


def _display(value: object, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send an actionable analytics report to Telegram.")
    parser.add_argument("--period", default="30d", help="Lookback: 24h, 7d, 4w, or all.")
    parser.add_argument("--format", choices=REPORT_FORMATS, default="text")
    parser.add_argument("--model", default=None, help="Exact model_name filter.")
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--timezone", default="Europe/Moscow")
    parser.add_argument("--negative-details-limit", type=int, default=10)
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    try:
        return asyncio.run(
            send_analytics_report(
                period=args.period,
                report_format=args.format,
                model=args.model,
                chat_id=args.chat_id,
                timezone_name=args.timezone,
                negative_details_limit=args.negative_details_limit,
            )
        )
    except Exception as exc:
        logger.error("Analytics report delivery failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
