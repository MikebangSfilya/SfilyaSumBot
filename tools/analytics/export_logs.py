# docker exec -it sumbot-bot-1 python -m tools.analytics.export_logs
# docker cp sumbot-bot-1:/app/summary_dataset.json .
# docker cp sumbot-bot-1:/app/summary_feedback_dataset.json .

import argparse
import asyncio
import os
from dotenv import load_dotenv

from tools.analytics.analytics_export import ANALYTICS_DATASETS, build_export_payloads, fetch_analytics_datasets

load_dotenv()


async def export_to_json(dataset: str = "all", summary_log_limit: int | None = None):
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL не найден в .env")
        return

    summary_rows, feedback_rows, bot_chat_rows, total_summary_logs = await fetch_analytics_datasets(
        db_url,
        summary_log_limit=summary_log_limit,
    )
    payloads = build_export_payloads(summary_rows, feedback_rows, bot_chat_rows, dataset)

    for file_name, payload in payloads.items():
        with open(file_name, "wb") as f:
            f.write(payload)

    if dataset in {"all", "summary"}:
        print(f"✅ Выгружено {len(summary_rows)} записей в summary_dataset.json")
        if summary_log_limit is not None:
            print(f"🔢 Лимит summary_logs в выгрузке: {summary_log_limit}")
        if total_summary_logs is not None:
            print(f"📈 Всего summary_logs за все время: {total_summary_logs}")
    if dataset in {"all", "feedback"}:
        print(f"✅ Выгружено {len(feedback_rows)} записей в summary_feedback_dataset.json")
    if dataset in {"all", "chats"}:
        print(f"✅ Выгружено {len(bot_chat_rows)} записей в bot_chats_dataset.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export analytics JSON files.")
    parser.add_argument(
        "--dataset",
        choices=ANALYTICS_DATASETS,
        default="all",
        help="Which analytics dataset to export.",
    )
    parser.add_argument(
        "--summary-limit",
        type=int,
        default=None,
        help="Export only the latest N summary_logs rows and feedback linked to those rows.",
    )
    args = parser.parse_args()
    asyncio.run(export_to_json(args.dataset, args.summary_limit))
