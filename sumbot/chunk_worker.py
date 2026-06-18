from __future__ import annotations

import asyncio
import json
import logging

import config
from sumbot.chunks import (
    CHUNK_QUEUE_KEY,
    build_chunk_source_payload,
    build_chunk_summary_record,
    fetch_chunk_summary_records,
    load_closed_chunk_payload,
    mark_chunk_summary_terminal_failure,
    requeue_chunk_summary_job,
    save_chunk_summary_record,
)
from sumbot.constants import CHUNK_SUMMARY_MAX_ATTEMPTS, CHUNK_WORKER_POLL_TIMEOUT_SECONDS
from sumbot.llm import generate_chunk_summary, load_chunk_prompt
from sumbot.logging_setup import configure_logging
from sumbot.services import BotServices, create_services
from sumbot.tracing import configure_tracing, shutdown_tracing

logger = logging.getLogger("SumBot.chunk_worker")


async def process_chunk_summary_job(
    services: BotServices,
    *,
    chat_id: int,
    chunk_id: str,
    attempts: int = 0,
    system_prompt: str | None = None,
) -> str:
    existing_records = await fetch_chunk_summary_records(services.redis, chat_id)
    if any(record.chunk_id == chunk_id for record in existing_records):
        logger.info("Chunk summary job skipped because summary already exists (chat_id=%s, chunk_id=%s)", chat_id, chunk_id)
        return "exists"

    payload = await load_closed_chunk_payload(services.redis, chat_id, chunk_id)
    if payload is None:
        await mark_chunk_summary_terminal_failure(services.redis, chat_id, chunk_id, "missing_payload")
        logger.warning("Chunk summary payload is missing (chat_id=%s, chunk_id=%s)", chat_id, chunk_id)
        return "missing_payload"

    chunk_model = await services.get_chunk_llm_model()
    prompt = system_prompt or load_chunk_prompt()
    try:
        source_payload = build_chunk_source_payload(payload.messages)
        summary_result = await generate_chunk_summary(
            chunk_model.client,
            chunk_model.option.model_name,
            source_payload,
            prompt,
            provider=chunk_model.option.provider,
        )
        record = build_chunk_summary_record(payload, summary_result.payload)
    except Exception:
        next_attempt = attempts + 1
        logger.exception(
            "Chunk summary job failed "
            "(chat_id=%s, chunk_id=%s, attempt=%s/%s)",
            chat_id,
            chunk_id,
            next_attempt,
            CHUNK_SUMMARY_MAX_ATTEMPTS,
        )
        if next_attempt < CHUNK_SUMMARY_MAX_ATTEMPTS:
            await requeue_chunk_summary_job(
                services.redis,
                chat_id,
                chunk_id,
                next_attempt,
                status="retry",
            )
            return "requeued"
        await mark_chunk_summary_terminal_failure(services.redis, chat_id, chunk_id, "failed")
        return "failed"

    await save_chunk_summary_record(services.redis, record)
    return "saved"


async def process_queue_forever(services: BotServices) -> None:
    prompt = load_chunk_prompt()
    while True:
        job_item = await services.redis.blpop(CHUNK_QUEUE_KEY, timeout=int(CHUNK_WORKER_POLL_TIMEOUT_SECONDS))
        if not job_item:
            continue
        _, payload = job_item
        try:
            job = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Malformed chunk summary queue job skipped: %r", payload)
            continue
        chat_id = job.get("chat_id")
        chunk_id = job.get("chunk_id")
        attempts = int(job.get("attempts", 0) or 0)
        if not isinstance(chat_id, int) or not isinstance(chunk_id, str) or not chunk_id:
            logger.warning("Chunk summary queue job is missing required fields: %r", job)
            continue
        await process_chunk_summary_job(
            services,
            chat_id=chat_id,
            chunk_id=chunk_id,
            attempts=attempts,
            system_prompt=prompt,
        )


async def run_worker() -> None:
    configure_logging()
    configure_tracing(
        enabled=config.TRACING_ENABLED,
        service_name=f"{config.TRACING_SERVICE_NAME}-chunk-worker",
        endpoint=config.TRACING_OTLP_ENDPOINT,
        sample_ratio=config.TRACING_SAMPLE_RATIO,
    )
    services = create_services()
    try:
        await process_queue_forever(services)
    finally:
        await services.close()
        shutdown_tracing()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
