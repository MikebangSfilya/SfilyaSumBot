import asyncio
import os
import argparse
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from openai import AsyncOpenAI

from sumbot.llm import generate_summary, load_prompt
from sumbot.summary_context import PreparedSummaryContext

logger = logging.getLogger("SumBot.benchmark")

BAD_CONTEXTS_QUERY = text("""
    SELECT sl.id,
           sl.chat_id,
           sl.model_name as original_model,
           sl.raw_context,
           sl.llm_response as original_response,
           sf.details as feedback_details
    FROM summary_logs sl
    JOIN summary_feedback sf ON sf.summary_log_id = sl.id
    WHERE sf.sentiment = 'negative'
      AND NULLIF(BTRIM(sf.details), '') IS NOT NULL
      AND NULLIF(BTRIM(sl.raw_context), '') IS NOT NULL
    ORDER BY sl.created_at DESC
    LIMIT :limit
""")


async def run_benchmark(args: argparse.Namespace) -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("Error: DATABASE_URL is required")
        return 1

    openai_api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("Error: OPENAI_API_KEY or OPENROUTER_API_KEY is required")
        return 1

    llm_client = AsyncOpenAI(
        api_key=openai_api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        result = await conn.execute(BAD_CONTEXTS_QUERY, {"limit": args.limit})
        bad_contexts = [dict(row._mapping) for row in result.fetchall()]

    if not bad_contexts:
        print(f"No bad contexts with feedback details found in database.")
        return 0

    print(f"Selected {len(bad_contexts)} bad contexts for rerunning.")
    
    system_prompt = load_prompt()
    results = []

    for item in bad_contexts:
        print(f"Rerunning summary for log_id={item['id']} (original_model={item['original_model']})...")
        
        # Prepare context (assuming raw_context is already anonymized text or similar)
        # We need turn_count to match the original logic if possible
        # For simplicity, we count lines or approximate turns
        turn_count = item["raw_context"].count("\nUser_") + 1
        prepared = PreparedSummaryContext(
            raw_message_count=0, # not used in generate_summary except for logs
            turn_count=turn_count,
            merged_count=0,
            rendered_text=item["raw_context"],
        )

        res = await generate_summary(
            llm_client=llm_client,
            model_name=args.candidate_model,
            prepared_context=prepared,
            system_prompt=system_prompt,
            chat_id=item["chat_id"],
        )

        if res:
            results.append({
                "log_id": item["id"],
                "original_model": item["original_model"],
                "candidate_model": args.candidate_model,
                "feedback_details": item["feedback_details"],
                "original_response": item["original_response"],
                "candidate_response": res.text,
            })
            print(f"Success for log_id={item['id']}")
        else:
            print(f"Failed for log_id={item['id']}")

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Results written to {args.output}")
    else:
        for r in results:
            print("-" * 40)
            print(f"Log ID: {r['log_id']}")
            print(f"Feedback: {r['feedback_details']}")
            print(f"Original ({r['original_model']}):\n{r['original_response']}")
            print(f"Candidate ({r['candidate_model']}):\n{r['candidate_response']}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Rerun bad contexts against a candidate model.")
    parser.add_argument("--candidate-model", required=True, help="Model to test.")
    parser.add_argument("--limit", type=int, default=5, help="Number of bad contexts to fetch.")
    parser.add_argument("--output", help="Path to save results as JSON.")
    
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
