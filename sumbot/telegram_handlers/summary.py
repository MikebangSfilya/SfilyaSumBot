import logging
import time
from dataclasses import dataclass
from typing import Any

from aiogram import Dispatcher, types
from aiogram.filters import Command
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncEngine

import anonymizer
import config
from sumbot.chat_registry import save_chat_snapshot
from sumbot.constants import (
    DEFAULT_SUMMARY_PERIOD_SECONDS,
    RATE_LIMIT_EXEMPT_USER_IDS,
    RATE_LIMIT_SECONDS,
    SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS,
    SUMMARY_FEEDBACK_KEYBOARD_TTL_SECONDS,
    SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS,
)
from sumbot.database import SummaryExample, fetch_random_good_summary_example, save_summary_analytics
from sumbot.engagement import mark_chat_activated, record_manual_summary_request
from sumbot.feedback import build_summary_feedback_keyboard
from sumbot.history import fetch_messages_for_summary
from sumbot.llm import SummaryResult, generate_summary, load_prompt, prepare_final_message
from sumbot.metrics import (
    mark_summary_result,
    observe_summary_context,
    summary_duration_timer,
)
from sumbot.prompt_builder import SummaryPresentationSettings, apply_summary_presentation
from sumbot.services import ActiveLlmModel, BotServices, SummaryGenerationSettings
from sumbot.summary_assembly import (
    SummarySourceBundle,
    build_chunk_native_prepared_context,
    fetch_summary_source_bundle,
    summarize_source_bundle_stats,
)
from sumbot.summary_context import PreparedSummaryContext, prepare_summary_context
from sumbot.telegram_handlers.common import schedule_remove_reply_markup_after_delay

logger = logging.getLogger("SumBot.telegram_handlers.summary")
tracer = trace.get_tracer(__name__)

SUMMARY_DEGRADED_MESSAGE_LIMITS = (50, 15)


@dataclass(slots=True)
class SummaryRequest:
    limit_messages: int | None
    time_limit_seconds: int | None


def register_summary_handlers(dispatcher: Dispatcher, services: BotServices) -> None:
    @dispatcher.message(Command("summary"))
    async def make_summary(message: types.Message) -> None:
        await save_chat_snapshot(services.db_engine, message.chat)
        with summary_duration_timer():
            with tracer.start_as_current_span(
                "telegram.summary",
                attributes={
                    "telegram.chat_id": message.chat.id,
                    "telegram.chat_type": message.chat.type,
                    "telegram.user_id": message.from_user.id if message.from_user else 0,
                },
            ):
                await process_summary_command(services, message)


async def process_summary_command(
    services: BotServices,
    message: types.Message,
    *,
    source: str = "manual",
    bypass_rate_limit: bool = False,
    summary_notice: str = "",
) -> str:
    summary_started_at = time.monotonic()
    if not message.from_user:
        logger.warning("Summary command ignored because from_user is missing (chat_id=%s)", message.chat.id)
        mark_summary_result("invalid_user")
        return "invalid_user"

    user_id = message.from_user.id
    chat_id = message.chat.id
    rate_limit_acquired = bypass_rate_limit or await acquire_summary_rate_limit(services, user_id, chat_id)
    if not rate_limit_acquired:
        logger.info("Rate limit triggered for user %s in chat %s", user_id, chat_id)
        await message.answer("⏳ Не спеши! Запрашивать пересказ можно только раз в минуту.")
        mark_summary_result("rate_limited")
        return "rate_limited"

    try:
        summary_request = parse_summary_request(message.text)
    except ValueError as exc:
        logger.warning(
            "Invalid summary command arguments (chat_id=%s, user_id=%s, command_chars=%s)",
            chat_id,
            user_id,
            len(message.text or ""),
        )
        await message.answer(str(exc))
        mark_summary_result("invalid_args")
        return "invalid_args"

    if source == "manual":
        await record_manual_summary_request(services.redis, chat_id)

    logger.info(
        "Starting summary for chat %s by user %s, limit=%s, time_limit=%s, source=%s",
        chat_id,
        user_id,
        summary_request.limit_messages,
        summary_request.time_limit_seconds,
        source,
    )

    try:
        with tracer.start_as_current_span(
            "summary.fetch_history",
            attributes={
                "summary.limit_messages": summary_request.limit_messages or 0,
                "summary.time_limit_seconds": summary_request.time_limit_seconds or 0,
                "summary.source": source,
            },
        ) as span:
            chunking_enabled = await services.is_chunking_enabled(chat_id)
            source_bundle = await fetch_messages_for_summary_request(
                services,
                chat_id,
                summary_request,
                chunking_enabled=chunking_enabled,
            )
            history_stats = summarize_source_bundle_stats(source_bundle)
            span.set_attribute("summary.source_messages", source_bundle.total_source_messages)
            span.set_attribute("summary.source_text_chars", history_stats["text_chars"])
            span.set_attribute("summary.source_unique_authors", history_stats["unique_authors"])
            span.set_attribute("summary.source_replies", history_stats["replies"])
            span.set_attribute("summary.source_timespan_seconds", history_stats["timespan_seconds"])
            span.set_attribute("summary.chunking.enabled", chunking_enabled)
            span.set_attribute("summary.chunking.chunk_count", len(source_bundle.chunk_records))
            span.set_attribute("summary.chunking.raw_tail_messages", len(source_bundle.raw_messages))
        if not await ensure_enough_messages(
            message,
            source_bundle.total_source_messages,
            summary_request,
            notify=source == "manual",
        ):
            mark_summary_result("not_enough_messages")
            return "not_enough_messages"

        wait_message = await message.answer("⏳ Сочиняю историю...")
        with tracer.start_as_current_span("summary.load_prompt") as span:
            base_system_prompt = load_prompt()
            presentation_settings = await services.get_summary_presentation_settings(chat_id)
            system_prompt = apply_summary_presentation(base_system_prompt, presentation_settings)
            span.set_attribute("summary.base_system_prompt_chars", len(base_system_prompt))
            span.set_attribute("summary.system_prompt_chars", len(system_prompt))
            span.set_attribute("summary.presentation.style", presentation_settings.style.option_id)
            span.set_attribute("summary.presentation.tone", presentation_settings.tone.option_id)
            span.set_attribute(
                "summary.presentation.aggressiveness",
                presentation_settings.aggressiveness.level,
            )
        with tracer.start_as_current_span("summary.choose_dynamic_example") as span:
            dynamic_example = await choose_dynamic_summary_example(
                services.db_engine,
                chat_id,
                presentation_settings,
            )
            span.set_attribute("summary.dynamic_example.selected", dynamic_example is not None)
        experiment_notice = "\n\n".join(
            notice for notice in (config.SUMMARY_EXPERIMENT_NOTICE, summary_notice) if notice
        )
        with tracer.start_as_current_span("summary.load_generation_settings") as span:
            model_chain = await services.get_llm_fallback_chain(
                chat_id,
                source_message_count=source_bundle.total_source_messages,
            )
            generation_settings = await services.get_summary_generation_settings(chat_id)
            span.set_attribute("summary.model_chain.length", len(model_chain))
            span.set_attribute("summary.model_chain.ids", [model.option.model_id for model in model_chain])
            span.set_attribute("summary.model_chain.providers", [model.option.provider for model in model_chain])
            span.set_attribute("summary.max_output_tokens", generation_settings.max_output_tokens or 0)
            span.set_attribute("summary.max_output_tokens.auto", generation_settings.max_output_tokens is None)
            span.set_attribute("summary.thinking_mode", generation_settings.thinking_mode)
        logger.info(
            "Summary model chain prepared "
            "(chat_id=%s, chain=%s, notice_enabled=%s, notice_chars=%s, "
            "max_output_tokens=%s, thinking_mode=%s, style=%s, tone=%s, aggressiveness=%s, "
            "chunk_records=%s, raw_tail_messages=%s)",
            chat_id,
            " -> ".join(model.option.model_id for model in model_chain),
            bool(experiment_notice),
            len(experiment_notice),
            generation_settings.max_output_tokens,
            generation_settings.thinking_mode,
            presentation_settings.style.option_id,
            presentation_settings.tone.option_id,
            presentation_settings.aggressiveness.level,
            len(source_bundle.chunk_records),
            len(source_bundle.raw_messages),
        )
        with tracer.start_as_current_span("summary.generate_with_fallbacks") as span:
            if source_bundle.is_chunk_native:
                summary, anon, used_context = await generate_chunk_native_summary_with_fallbacks(
                    model_chain,
                    source_bundle,
                    system_prompt,
                    chat_id,
                    dynamic_example=dynamic_example,
                    generation_settings=generation_settings,
                )
            else:
                summary, anon, used_context = await generate_summary_with_fallbacks(
                    model_chain,
                    source_bundle.raw_messages,
                    system_prompt,
                    chat_id,
                    dynamic_example=dynamic_example,
                    generation_settings=generation_settings,
                )
            span.set_attribute("summary.result.present", bool(summary and summary.text))
            span.set_attribute("summary.result", "success" if summary and summary.text else "failed")
            if used_context:
                span.set_attribute("summary.context.raw_messages", used_context.raw_message_count)
                span.set_attribute("summary.context.turns", used_context.turn_count)
                span.set_attribute("summary.context.merged", used_context.merged_count)
                span.set_attribute("summary.context.rendered_chars", len(used_context.rendered_text))
            if summary:
                span.set_attribute("llm.model", summary.model_name)
                span.set_attribute("llm.provider", summary.provider)
                span.set_attribute("llm.attempt", summary.attempt)
                span.set_attribute("llm.elapsed_seconds", summary.elapsed_seconds)
                span.set_attribute("llm.finish_reason", summary.finish_reason)
                span.set_attribute("llm.input_tokens", summary.input_tokens)
                span.set_attribute("llm.output_tokens", summary.output_tokens)
                span.set_attribute("llm.total_tokens", summary.input_tokens + summary.output_tokens)
                span.set_attribute("llm.reasoning_tokens", summary.reasoning_tokens)
                span.set_attribute("llm.summary_chars", len(summary.text))

        if not summary or not summary.text:
            logger.error("LLM fallback chain failed for chat %s", chat_id)
            await wait_message.edit_text("⚠️ Не удалось связаться с нейросетью или превышены лимиты запросов.")
            mark_summary_result("llm_error")
            return "llm_error"

        with tracer.start_as_current_span("summary.prepare_final_message") as span:
            final_text = prepare_final_message(
                summary.text,
                anon,
                experiment_notice=experiment_notice,
            )
            span.set_attribute("summary.final_text_chars", len(final_text))
        with tracer.start_as_current_span("telegram.send_summary"):
            await send_summary(wait_message, final_text, chat_id)
        with tracer.start_as_current_span("db.save_summary_analytics") as span:
            await save_summary_analytics(
                services.db_engine,
                chat_id,
                wait_message.message_id,
                system_prompt,
                summary.anonymized_context,
                summary.text,
                summary.model_name,
                summary.input_tokens,
                summary.output_tokens,
                time.monotonic() - summary_started_at,
                summary.elapsed_seconds,
                presentation_settings,
                trigger_source=source,
            )
            span.set_attribute("llm.model", summary.model_name)
            span.set_attribute("llm.provider", summary.provider)
            span.set_attribute("llm.elapsed_seconds", summary.elapsed_seconds)
            span.set_attribute("llm.finish_reason", summary.finish_reason)
            span.set_attribute("llm.input_tokens", summary.input_tokens)
            span.set_attribute("llm.output_tokens", summary.output_tokens)
            span.set_attribute("llm.total_tokens", summary.input_tokens + summary.output_tokens)
        logger.info(
            "Summary flow completed "
            "(chat_id=%s, user_id=%s, telegram_message_id=%s, context_turns=%s)",
            chat_id,
            user_id,
            wait_message.message_id,
            used_context.turn_count if used_context else None,
        )
        await mark_chat_activated(services.redis, chat_id)
        mark_summary_result("success")
        return "success"
    except Exception as exc:
        logger.error("Summary error: %s", exc, exc_info=True)
        await message.answer("⚠️ Что-то пошло не так.")
        mark_summary_result("internal_error")
        return "internal_error"


async def choose_dynamic_summary_example(
    db_engine: AsyncEngine | None,
    chat_id: int,
    presentation_settings: SummaryPresentationSettings,
) -> SummaryExample | None:
    if not config.SUMMARY_DYNAMIC_EXAMPLES_ENABLED:
        logger.info("Dynamic summary examples disabled (chat_id=%s)", chat_id)
        return None
    dynamic_example = await fetch_random_good_summary_example(db_engine, presentation_settings)
    logger.info(
        "Dynamic summary example state "
        "(chat_id=%s, selected=%s, summary_log_id=%s, style=%s, tone=%s, aggressiveness=%s)",
        chat_id,
        dynamic_example is not None,
        dynamic_example.summary_log_id if dynamic_example else None,
        presentation_settings.style.option_id,
        presentation_settings.tone.option_id,
        presentation_settings.aggressiveness.level,
    )
    return dynamic_example


async def generate_summary_with_fallbacks(
    model_chain: tuple[ActiveLlmModel, ...],
    messages: list[dict],
    system_prompt: str,
    chat_id: int,
    dynamic_example: SummaryExample | None = None,
    generation_settings: SummaryGenerationSettings | None = None,
) -> tuple[SummaryResult | None, anonymizer.Anonymizer | None, PreparedSummaryContext | None]:
    generation_settings = generation_settings or SummaryGenerationSettings()
    deadline = time.monotonic() + SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS
    logger.info(
        "Summary fallback budget started "
        "(chat_id=%s, total_timeout=%.2fs, model_count=%s, source_messages=%s, "
        "max_output_tokens=%s, thinking_mode=%s)",
        chat_id,
        SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS,
        len(model_chain),
        len(messages),
        generation_settings.max_output_tokens,
        generation_settings.thinking_mode,
    )
    fallback_started_at = time.monotonic()
    context_variants = build_summary_message_variants(messages)
    current_span = trace.get_current_span()
    current_span.set_attribute("summary.fallback.total_timeout_seconds", SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS)
    current_span.set_attribute("summary.fallback.variant_count", len(context_variants))
    current_span.set_attribute("summary.fallback.model_count", len(model_chain))
    current_span.set_attribute("summary.fallback.planned_attempts", len(context_variants) * len(model_chain))
    llm_attempts = 0

    for degradation_level, candidate_messages in enumerate(context_variants, start=1):
        anon = anonymizer.Anonymizer()
        candidate_stats = summarize_history_stats(candidate_messages)
        with tracer.start_as_current_span(
            "summary.prepare_context",
            attributes={
                "summary.degradation_level": degradation_level,
                "summary.candidate_messages": len(candidate_messages),
                "summary.candidate_text_chars": candidate_stats["text_chars"],
                "summary.candidate_unique_authors": candidate_stats["unique_authors"],
                "summary.candidate_replies": candidate_stats["replies"],
                "summary.candidate_timespan_seconds": candidate_stats["timespan_seconds"],
                "summary.context_v2_enabled": config.SUMMARY_CONTEXT_V2_ENABLED,
            },
        ) as span:
            prepared_context = prepare_summary_context(
                candidate_messages,
                anon,
                enable_v2=config.SUMMARY_CONTEXT_V2_ENABLED,
            )
            span.set_attribute("summary.context.raw_messages", prepared_context.raw_message_count)
            span.set_attribute("summary.context.turns", prepared_context.turn_count)
            span.set_attribute("summary.context.merged", prepared_context.merged_count)
            span.set_attribute("summary.context.rendered_chars", len(prepared_context.rendered_text))
            span.set_attribute("summary.context.merge_ratio", calculate_merge_ratio(prepared_context))
        if not prepared_context.rendered_text:
            current_span.set_attribute("summary.fallback.last_result", "empty_context")
            logger.warning(
                "Prepared summary context is empty during fallback attempt "
                "(chat_id=%s, degradation_level=%s, source_messages=%s)",
                chat_id,
                degradation_level,
                len(candidate_messages),
            )
            continue

        observe_summary_context(
            prepared_context.raw_message_count,
            prepared_context.turn_count,
            prepared_context.merged_count,
        )
        for chain_index, active_model in enumerate(model_chain, start=1):
            remaining_timeout_seconds = deadline - time.monotonic()
            if remaining_timeout_seconds < SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS:
                logger.warning(
                    "Summary fallback budget exhausted "
                    "(chat_id=%s, degradation_level=%s, chain_index=%s/%s, "
                    "remaining_timeout=%.2fs, min_attempt_timeout=%.2fs)",
                    chat_id,
                    degradation_level,
                    chain_index,
                    len(model_chain),
                    remaining_timeout_seconds,
                    SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS,
                )
                current_span.set_attribute("summary.fallback.result", "deadline_exhausted")
                current_span.set_attribute("summary.fallback.llm_attempts", llm_attempts)
                current_span.set_attribute("summary.fallback.elapsed_seconds", time.monotonic() - fallback_started_at)
                return None, None, None

            logger.info(
                "Summary LLM attempt "
                "(chat_id=%s, degradation_level=%s, chain_index=%s/%s, v2_enabled=%s, "
                "raw_messages=%s, turns=%s, merged=%s, model_id=%s, provider=%s, model=%s, "
                "remaining_timeout=%.2fs)",
                chat_id,
                degradation_level,
                chain_index,
                len(model_chain),
                config.SUMMARY_CONTEXT_V2_ENABLED,
                prepared_context.raw_message_count,
                prepared_context.turn_count,
                prepared_context.merged_count,
                active_model.option.model_id,
                active_model.option.provider,
                active_model.option.model_name,
                remaining_timeout_seconds,
            )
            llm_attempts += 1
            attempt_started_at = time.monotonic()
            summary = await generate_summary(
                active_model.client,
                active_model.option.model_name,
                prepared_context,
                system_prompt,
                chat_id,
                dynamic_example=dynamic_example,
                provider=active_model.option.provider,
                deadline=deadline,
                max_output_tokens_override=generation_settings.max_output_tokens,
                thinking_mode=generation_settings.thinking_mode,
            )
            if summary and summary.text:
                current_span.set_attribute("summary.fallback.result", "success")
                current_span.set_attribute("summary.fallback.degradation_level", degradation_level)
                current_span.set_attribute("summary.fallback.chain_index", chain_index)
                current_span.set_attribute("summary.fallback.llm_attempts", llm_attempts)
                current_span.set_attribute("summary.fallback.elapsed_seconds", time.monotonic() - fallback_started_at)
                current_span.set_attribute("summary.fallback.used_fallback", degradation_level > 1 or chain_index > 1)
                current_span.set_attribute("summary.fallback.winning_model_id", active_model.option.model_id)
                current_span.set_attribute("summary.fallback.winning_provider", active_model.option.provider)
                current_span.set_attribute("summary.fallback.last_llm_elapsed_seconds", time.monotonic() - attempt_started_at)
                if degradation_level > 1 or chain_index > 1:
                    logger.warning(
                        "Summary recovered by fallback "
                        "(chat_id=%s, degradation_level=%s, chain_index=%s, model_id=%s)",
                        chat_id,
                        degradation_level,
                        chain_index,
                        active_model.option.model_id,
                    )
                return summary, anon, prepared_context

            logger.warning(
                "Summary LLM attempt produced no result "
                "(chat_id=%s, degradation_level=%s, chain_index=%s, model_id=%s)",
                chat_id,
                degradation_level,
                chain_index,
                active_model.option.model_id,
            )
            current_span.set_attribute("summary.fallback.last_result", "no_summary")

    current_span.set_attribute("summary.fallback.result", "failed")
    current_span.set_attribute("summary.fallback.llm_attempts", llm_attempts)
    current_span.set_attribute("summary.fallback.elapsed_seconds", time.monotonic() - fallback_started_at)
    return None, None, None


async def generate_chunk_native_summary_with_fallbacks(
    model_chain: tuple[ActiveLlmModel, ...],
    source_bundle: SummarySourceBundle,
    system_prompt: str,
    chat_id: int,
    dynamic_example: SummaryExample | None = None,
    generation_settings: SummaryGenerationSettings | None = None,
) -> tuple[SummaryResult | None, anonymizer.Anonymizer | None, PreparedSummaryContext | None]:
    generation_settings = generation_settings or SummaryGenerationSettings()
    deadline = time.monotonic() + SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS
    fallback_started_at = time.monotonic()
    prepared_context, anon = build_chunk_native_prepared_context(
        source_bundle,
        enable_context_v2=config.SUMMARY_CONTEXT_V2_ENABLED,
    )
    if not prepared_context.rendered_text:
        return None, None, None

    observe_summary_context(
        prepared_context.raw_message_count,
        prepared_context.turn_count,
        prepared_context.merged_count,
    )
    current_span = trace.get_current_span()
    current_span.set_attribute("summary.fallback.total_timeout_seconds", SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS)
    current_span.set_attribute("summary.fallback.variant_count", 1)
    current_span.set_attribute("summary.fallback.model_count", len(model_chain))
    current_span.set_attribute("summary.fallback.planned_attempts", len(model_chain))
    current_span.set_attribute("summary.chunk_native", True)
    llm_attempts = 0

    chunk_native_prompt = (
        f"{system_prompt}\n\n"
        "[CHUNK-NATIVE CONTEXT]: Внутри <precomputed_chunk_summaries> лежат уже подготовленные factual summaries "
        "старого контекста. Не пересказывай технические заголовки чанков и не делай вид, что это сырой лог. "
        "Внутри <live_chat_tail> лежит свежий сырой хвост чата; он приоритетнее по новизне."
    )

    for chain_index, active_model in enumerate(model_chain, start=1):
        remaining_timeout_seconds = deadline - time.monotonic()
        if remaining_timeout_seconds < SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS:
            current_span.set_attribute("summary.fallback.result", "deadline_exhausted")
            current_span.set_attribute("summary.fallback.llm_attempts", llm_attempts)
            current_span.set_attribute("summary.fallback.elapsed_seconds", time.monotonic() - fallback_started_at)
            return None, None, None

        llm_attempts += 1
        summary = await generate_summary(
            active_model.client,
            active_model.option.model_name,
            prepared_context,
            chunk_native_prompt,
            chat_id,
            dynamic_example=dynamic_example,
            provider=active_model.option.provider,
            deadline=deadline,
            max_output_tokens_override=generation_settings.max_output_tokens,
            thinking_mode=generation_settings.thinking_mode,
        )
        if summary and summary.text:
            current_span.set_attribute("summary.fallback.result", "success")
            current_span.set_attribute("summary.fallback.chain_index", chain_index)
            current_span.set_attribute("summary.fallback.llm_attempts", llm_attempts)
            current_span.set_attribute("summary.fallback.elapsed_seconds", time.monotonic() - fallback_started_at)
            current_span.set_attribute("summary.fallback.used_fallback", chain_index > 1)
            current_span.set_attribute("summary.fallback.winning_model_id", active_model.option.model_id)
            current_span.set_attribute("summary.fallback.winning_provider", active_model.option.provider)
            return summary, anon, prepared_context

        current_span.set_attribute("summary.fallback.last_result", "no_summary")

    current_span.set_attribute("summary.fallback.result", "failed")
    current_span.set_attribute("summary.fallback.llm_attempts", llm_attempts)
    current_span.set_attribute("summary.fallback.elapsed_seconds", time.monotonic() - fallback_started_at)
    return None, None, None


def summarize_history_stats(messages: list[dict[str, Any]]) -> dict[str, int]:
    timestamps: list[float] = []
    author_keys: set[str] = set()
    text_chars = 0
    replies = 0

    for item in messages:
        text = item.get("message_text")
        if not isinstance(text, str):
            text = item.get("text")
        if isinstance(text, str):
            text_chars += len(text)

        ts = item.get("ts")
        if isinstance(ts, int | float):
            timestamps.append(float(ts))

        author_key = build_non_pii_author_key(item)
        if author_key:
            author_keys.add(author_key)

        if item.get("reply_to_user_id") or item.get("reply_to_username") or item.get("reply_to_name"):
            replies += 1

    timespan_seconds = int(max(timestamps) - min(timestamps)) if len(timestamps) >= 2 else 0
    return {
        "text_chars": text_chars,
        "unique_authors": len(author_keys),
        "replies": replies,
        "timespan_seconds": max(timespan_seconds, 0),
    }


def build_non_pii_author_key(item: dict[str, Any]) -> str:
    author_id = item.get("author_id")
    if isinstance(author_id, int):
        return f"id:{author_id}"

    author_username = item.get("author_username")
    if isinstance(author_username, str) and author_username:
        return f"username:{author_username}"

    author_name = item.get("author_name")
    if isinstance(author_name, str) and author_name:
        return f"name:{author_name}"

    text = item.get("text")
    if isinstance(text, str):
        parsed = text.split(":", 1)
        if len(parsed) == 2 and parsed[0].strip():
            return f"legacy:{parsed[0].strip()}"

    return ""


def calculate_merge_ratio(prepared_context: PreparedSummaryContext) -> float:
    if prepared_context.raw_message_count <= 0:
        return 0.0
    return round(prepared_context.merged_count / prepared_context.raw_message_count, 4)


def build_summary_message_variants(messages: list[dict]) -> list[list[dict]]:
    variants = [messages]
    seen_lengths = {len(messages)}
    for limit in SUMMARY_DEGRADED_MESSAGE_LIMITS:
        if len(messages) <= limit or limit in seen_lengths:
            continue
        variants.append(messages[-limit:])
        seen_lengths.add(limit)
    return variants


def parse_summary_request(text: str | None) -> SummaryRequest:
    args = (text or "").split()
    if len(args) <= 1:
        return SummaryRequest(
            limit_messages=None,
            time_limit_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS,
        )

    try:
        limit_messages = int(args[1])
    except ValueError as exc:
        raise ValueError("❌ Аргумент должен быть числом (например, /summary 100).") from exc

    if limit_messages <= 0:
        raise ValueError("❌ Число сообщений должно быть положительным.")

    logger.info("User requested summary of last %s messages (no time limit)", limit_messages)
    return SummaryRequest(limit_messages=limit_messages, time_limit_seconds=None)


async def acquire_summary_rate_limit(services: BotServices, user_id: int, chat_id: int) -> bool:
    if user_id in RATE_LIMIT_EXEMPT_USER_IDS:
        logger.debug("Summary rate limit bypassed for exempt user (chat_id=%s, user_id=%s)", chat_id, user_id)
        return True

    rate_key = f"rate_limit:{user_id}:{chat_id}"
    acquired = bool(await services.redis.set(rate_key, "1", nx=True, ex=RATE_LIMIT_SECONDS))
    logger.debug("Summary rate limit checked (chat_id=%s, user_id=%s, acquired=%s)", chat_id, user_id, acquired)
    return acquired


async def ensure_enough_messages(
    message: types.Message,
    message_count: int,
    summary_request: SummaryRequest,
    *,
    notify: bool = True,
) -> bool:
    if message_count <= 0:
        logger.warning("No messages found for chat %s", message.chat.id)
        if notify:
            await message.answer("Чат пуст.")
        return False

    min_required = 1 if summary_request.limit_messages is not None else 5
    if message_count >= min_required:
        return True

    logger.warning("Too few messages (%s) for chat %s", message_count, message.chat.id)
    if not notify:
        return False
    if summary_request.limit_messages is not None:
        await message.answer(
            f"Запрошено {summary_request.limit_messages} сообщений, но найдено только {message_count}."
        )
    else:
        await message.answer("Маловато сообщений для истории за последние 24 часа.")
    return False


async def send_summary(wait_message: types.Message, final_text: str, chat_id: int) -> None:
    try:
        logger.info(
            "Sending summary to Telegram (chat_id=%s, message_id=%s, html_chars=%s)",
            chat_id,
            wait_message.message_id,
            len(final_text),
        )
        await wait_message.edit_text(
            final_text,
            parse_mode="HTML",
            reply_markup=build_summary_feedback_keyboard(),
        )
        schedule_remove_reply_markup_after_delay(
            wait_message,
            delay_seconds=SUMMARY_FEEDBACK_KEYBOARD_TTL_SECONDS,
        )
        logger.info("Summary successfully sent to chat %s", chat_id)
    except Exception as exc:
        logger.error("TG Send Error. Length: %s. Error: %s", len(final_text), exc)
        raise


async def fetch_messages_for_summary_request(
    services: BotServices,
    chat_id: int,
    summary_request: SummaryRequest,
    *,
    chunking_enabled: bool,
) -> SummarySourceBundle:
    raw_messages = await fetch_messages_for_summary(
        services.redis,
        chat_id,
        summary_request.limit_messages,
        summary_request.time_limit_seconds,
    )
    return await fetch_summary_source_bundle(
        services.redis,
        chat_id,
        raw_messages=raw_messages,
        limit_messages=summary_request.limit_messages,
        time_limit_seconds=summary_request.time_limit_seconds,
        current_ts=time.time(),
        chunking_enabled=chunking_enabled,
    )
