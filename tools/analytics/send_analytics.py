import argparse
import asyncio
import logging
import os
import sys

from aiogram import Bot
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

from tools.analytics.analytics_export import (
    ANALYTICS_DATASETS,
    AnalyticsDataset,
    build_export_caption,
    build_export_payloads,
    fetch_analytics_datasets,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("send_analytics")


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


async def send_analytics(dataset: AnalyticsDataset = "all", summary_log_limit: int | None = None) -> int:
    db_url = _require_env("DATABASE_URL")
    tg_token = _require_env("TG_TOKEN")
    analytics_chat_id = _parse_chat_id(_require_env("ANALYTICS_CHAT_ID"))

    summary_rows, feedback_rows, bot_chat_rows, total_summary_logs = await fetch_analytics_datasets(
        db_url,
        summary_log_limit=summary_log_limit,
    )
    payloads = build_export_payloads(summary_rows, feedback_rows, bot_chat_rows, dataset)
    caption = build_export_caption(
        summary_rows,
        feedback_rows,
        bot_chat_rows,
        dataset,
        total_summary_logs,
        summary_log_limit=summary_log_limit,
    )

    bot = Bot(token=tg_token)
    try:
        await bot.send_message(analytics_chat_id, caption)

        for file_name, payload in payloads.items():
            await bot.send_document(
                chat_id=analytics_chat_id,
                document=BufferedInputFile(payload, filename=file_name),
            )
            logger.info("Sent %s to chat %s", file_name, analytics_chat_id)

        logger.info(
            "Analytics export sent successfully: "
            "dataset=%s summary_logs=%s summary_logs_total=%s summary_feedback=%s bot_chats=%s summary_log_limit=%s",
            dataset,
            len(summary_rows),
            total_summary_logs,
            len(feedback_rows),
            len(bot_chat_rows),
            summary_log_limit,
        )
        return 0
    finally:
        await bot.session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send analytics JSON exports to Telegram.")
    parser.add_argument(
        "--dataset",
        choices=ANALYTICS_DATASETS,
        default="all",
        help="Which analytics dataset to send.",
    )
    parser.add_argument(
        "--summary-limit",
        type=int,
        default=None,
        help="Send only the latest N summary_logs rows and feedback linked to those rows.",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(send_analytics(args.dataset, args.summary_limit))
    except Exception as exc:
        logger.error("Analytics export failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
