import asyncio
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from opentelemetry import trace

from anonymizer import Anonymizer
from sumbot.metrics import observe_llm_request
from sumbot.constants import (
    CHUNK_SUMMARY_MAX_OUTPUT_TOKENS,
    CHUNK_SUMMARY_TIMEOUT_SECONDS,
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_MEDIUM_LOG_MAX_OUTPUT_TOKENS,
    SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS,
    SUMMARY_RETRY_BACKOFF_SECONDS,
    SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS,
    SUMMARY_TIMEOUT_SECONDS,
)
from sumbot.summary_context import PreparedSummaryContext
from sumbot.summary_validation import build_validation_retry_instruction, validate_summary_output

if TYPE_CHECKING:
    from sumbot.database import SummaryExample

logger = logging.getLogger("SumBot.llm")
tracer = trace.get_tracer(__name__)

EXAMPLE_BLOCK_PATTERN = re.compile(r"<example>.*?</example>", flags=re.DOTALL | re.IGNORECASE)
ROLE_TAG_LEAK_PATTERN = re.compile(r"(User_\d+)\s*\[(?:[а-яё -]+|в ответ User_\d+|отвечает User_\d+)\]")
JSON_CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*\n?(?P<body>.*?)(?:\n)?```$", re.DOTALL | re.IGNORECASE)


@dataclass(slots=True)
class SummaryResult:
    text: str
    model_name: str
    input_tokens: int
    output_tokens: int
    anonymized_context: str
    provider: str = ""
    attempt: int = 0
    elapsed_seconds: float = 0.0
    finish_reason: str = ""
    reasoning_tokens: int = 0


@dataclass(slots=True)
class ChunkSummaryResult:
    payload: dict
    model_name: str
    input_tokens: int
    output_tokens: int
    provider: str = ""
    elapsed_seconds: float = 0.0
    finish_reason: str = ""


def load_prompt(path: str = "prompt.md") -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as prompt_file:
            prompt = prompt_file.read()
        logger.info("Loaded system prompt (path=%s, chars=%s)", path, len(prompt))
        return prompt
    logger.warning("System prompt file not found (path=%s); using fallback prompt.", path)
    return "Ты — остроумный участник чата. Выдай базу о том, что тут происходило."


def load_chunk_prompt(path: str = "chunk_prompt.md") -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as prompt_file:
            prompt = prompt_file.read()
        logger.info("Loaded chunk prompt (path=%s, chars=%s)", path, len(prompt))
        return prompt
    logger.warning("Chunk prompt file not found (path=%s); using fallback chunk prompt.", path)
    return (
        "Ты сжимаешь кусок чата в строгий JSON. Верни только JSON-объект "
        "с полями topics, events и open_loops."
    )


async def generate_chunk_summary(
    llm_client: AsyncOpenAI,
    model_name: str,
    chunk_payload: dict,
    system_prompt: str,
    *,
    provider: str | None = None,
) -> ChunkSummaryResult:
    started_at = time.monotonic()
    response = await asyncio.wait_for(
        llm_client.chat.completions.create(
            **build_chunk_chat_completion_kwargs(
                model_name=model_name,
                system_prompt=system_prompt,
                chunk_payload=chunk_payload,
                provider=provider,
            ),
        ),
        timeout=CHUNK_SUMMARY_TIMEOUT_SECONDS,
    )
    choice = response.choices[0]
    message = choice.message
    finish_reason = getattr(choice, "finish_reason", None) or ""
    payload_text = strip_json_code_fence(strip_thinking_tags(getattr(message, "content", None)))
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Chunk summary response is not valid JSON: {payload_text[:240]}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Chunk summary response must be a JSON object")

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "Chunk summary request finished "
        "(model=%s, provider=%s, finish_reason=%s, input_tokens=%s, output_tokens=%s, elapsed=%.2fs)",
        model_name,
        provider,
        finish_reason,
        input_tokens,
        output_tokens,
        elapsed_seconds,
    )
    return ChunkSummaryResult(
        payload=payload,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider=provider or "",
        elapsed_seconds=elapsed_seconds,
        finish_reason=finish_reason,
    )


async def generate_summary(
    llm_client: AsyncOpenAI,
    model_name: str,
    prepared_context: PreparedSummaryContext,
    system_prompt: str,
    chat_id: int,
    dynamic_example: "SummaryExample | None" = None,
    provider: str | None = None,
    deadline: float | None = None,
    max_output_tokens_override: int | None = None,
    thinking_mode: str = "disabled",
) -> SummaryResult | None:
    anonymized_text = prepared_context.rendered_text
    max_output_tokens = max_output_tokens_override or get_summary_max_tokens(prepared_context.turn_count)
    max_attempts = len(SUMMARY_RETRY_BACKOFF_SECONDS)
    validation_retry_instruction = ""
    validation_retry_used = False
    logger.info(
        "Preparing LLM summary "
        "(chat_id=%s, model=%s, raw_messages=%s, turns=%s, merged=%s, anonymized_chars=%s, "
        "timeout_seconds=%s, max_tokens=%s, thinking_mode=%s)",
        chat_id,
        model_name,
        prepared_context.raw_message_count,
        prepared_context.turn_count,
        prepared_context.merged_count,
        len(anonymized_text),
        int(SUMMARY_TIMEOUT_SECONDS),
        max_output_tokens,
        thinking_mode,
    )

    for attempt, delay in enumerate(SUMMARY_RETRY_BACKOFF_SECONDS, start=1):
        current_system_prompt = build_system_prompt(
            system_prompt,
            prepared_context.turn_count,
            chat_id,
            dynamic_example=dynamic_example,
        )
        if validation_retry_instruction:
            current_system_prompt = f"{current_system_prompt}\n\n{validation_retry_instruction}"
        attempt_timeout_seconds = get_llm_attempt_timeout_seconds(deadline)
        if attempt_timeout_seconds < SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS:
            logger.warning(
                "LLM summary deadline exhausted before request "
                "(chat_id=%s, model=%s, attempt=%s/%s, remaining_timeout=%.2fs)",
                chat_id,
                model_name,
                attempt,
                max_attempts,
                attempt_timeout_seconds,
            )
            break

        with tracer.start_as_current_span(
            "llm.chat_completion",
            attributes={
                "llm.model": model_name,
                "llm.provider": provider or "",
                "llm.attempt": attempt,
                "llm.max_attempts": max_attempts,
                "llm.request_timeout_seconds": attempt_timeout_seconds,
                "llm.max_output_tokens": max_output_tokens,
                "summary.context.raw_messages": prepared_context.raw_message_count,
                "summary.context.turns": prepared_context.turn_count,
                "summary.context.merged": prepared_context.merged_count,
                "summary.anonymized_chars": len(anonymized_text),
                "summary.system_prompt_chars": len(current_system_prompt),
                "summary.thinking_mode": thinking_mode,
                "summary.dynamic_example.enabled": dynamic_example is not None,
            },
        ) as span:
            attempt_started_at = time.monotonic()
            logger.info(
                "LLM summary request started "
                "(chat_id=%s, model=%s, attempt=%s/%s, system_prompt_chars=%s, request_timeout=%.2fs)",
                chat_id,
                model_name,
                attempt,
                max_attempts,
                len(current_system_prompt),
                attempt_timeout_seconds,
            )
            try:
                response = await asyncio.wait_for(
                    llm_client.chat.completions.create(
                        **build_chat_completion_kwargs(
                            model_name=model_name,
                            system_prompt=current_system_prompt,
                            anonymized_text=anonymized_text,
                            max_output_tokens=max_output_tokens,
                            provider=provider,
                            thinking_mode=thinking_mode,
                        ),
                    ),
                    timeout=attempt_timeout_seconds,
                )

                choice = response.choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                message = choice.message
                summary_text = strip_thinking_tags(getattr(message, "content", None))
                elapsed_seconds = time.monotonic() - attempt_started_at
                usage = response.usage
                input_tokens = usage.prompt_tokens if usage else 0
                output_tokens = usage.completion_tokens if usage else 0
                reasoning_tokens = get_reasoning_tokens(usage)
                reasoning_chars = len(getattr(message, "reasoning_content", "") or "")
                total_tokens = input_tokens + output_tokens
                span.set_attribute("llm.finish_reason", finish_reason or "")
                span.set_attribute("llm.input_tokens", input_tokens)
                span.set_attribute("llm.output_tokens", output_tokens)
                span.set_attribute("llm.total_tokens", total_tokens)
                span.set_attribute("llm.reasoning_tokens", reasoning_tokens)
                span.set_attribute("llm.reasoning_chars", reasoning_chars)
                span.set_attribute("llm.elapsed_seconds", elapsed_seconds)
                span.set_attribute("llm.summary_chars", len(summary_text))
                span.set_attribute("llm.output_tokens_per_second", calculate_rate(output_tokens, elapsed_seconds))
                span.set_attribute("llm.total_tokens_per_second", calculate_rate(total_tokens, elapsed_seconds))
                span.set_attribute("summary.input_chars_per_token", calculate_rate(len(anonymized_text), input_tokens))
                span.set_attribute("summary.output_chars_per_token", calculate_rate(len(summary_text), output_tokens))
                if finish_reason == "length":
                    span.set_attribute("llm.result", "truncated")
                    observe_llm_request(
                        "truncated",
                        elapsed_seconds,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    logger.warning(
                        "LLM summary was truncated by token limit "
                        "(chat_id=%s, model=%s, attempt=%s/%s, input_tokens=%s, output_tokens=%s, "
                        "reasoning_tokens=%s, reasoning_chars=%s, visible_chars=%s)",
                        chat_id,
                        model_name,
                        attempt,
                        max_attempts,
                        input_tokens,
                        output_tokens,
                        reasoning_tokens,
                        reasoning_chars,
                        len(summary_text),
                    )
                    break
                if not summary_text:
                    span.set_attribute("llm.result", "empty")
                    observe_llm_request("empty", elapsed_seconds)
                    logger.warning(
                        "LLM returned an empty summary "
                        "(chat_id=%s, model=%s, attempt=%s/%s, elapsed=%.2fs)",
                        chat_id,
                        model_name,
                        attempt,
                        max_attempts,
                        elapsed_seconds,
                    )
                    if attempt < max_attempts:
                        logger.info("Retrying LLM request after empty summary in %s seconds.", delay)
                        if await sleep_before_retry(delay, deadline):
                            continue
                    break

                validation = validate_summary_output(summary_text, anonymized_text)
                if not validation.is_valid:
                    span.set_attribute("llm.result", "invalid_output")
                    span.set_attribute("summary.validation.reasons", list(validation.reasons))
                    observe_llm_request(
                        "invalid_output",
                        elapsed_seconds,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    logger.warning(
                        "LLM summary rejected by output validator "
                        "(chat_id=%s, model=%s, attempt=%s/%s, reasons=%s, elapsed=%.2fs)",
                        chat_id,
                        model_name,
                        attempt,
                        max_attempts,
                        ",".join(validation.reasons),
                        elapsed_seconds,
                    )
                    if not validation_retry_used and attempt < max_attempts:
                        validation_retry_used = True
                        validation_retry_instruction = build_validation_retry_instruction(validation.reasons)
                        logger.info(
                            "Retrying LLM request after output validation failure "
                            "(chat_id=%s, model=%s)",
                            chat_id,
                            model_name,
                        )
                        continue
                    break

                logger.info(
                    "LLM summary request finished "
                    "(chat_id=%s, model=%s, attempt=%s/%s, finish_reason=%s, input_tokens=%s, "
                    "output_tokens=%s, reasoning_tokens=%s, elapsed=%.2fs)",
                    chat_id,
                    model_name,
                    attempt,
                    max_attempts,
                    finish_reason,
                    input_tokens,
                    output_tokens,
                    reasoning_tokens,
                    elapsed_seconds,
                )
                span.set_attribute("llm.result", "success")
                observe_llm_request(
                    "success",
                    elapsed_seconds,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                return SummaryResult(
                    text=summary_text,
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    anonymized_context=anonymized_text,
                    provider=provider or "",
                    attempt=attempt,
                    elapsed_seconds=elapsed_seconds,
                    finish_reason=finish_reason or "",
                    reasoning_tokens=reasoning_tokens,
                )
            except Exception as exc:
                elapsed_seconds = time.monotonic() - attempt_started_at
                error_summary = summarize_exception_for_log(exc)
                error_type = classify_llm_error(exc)
                span.set_attribute("llm.result", error_type)
                span.set_attribute("error.type", type(exc).__name__)
                span.set_attribute("llm.elapsed_seconds", elapsed_seconds)
                observe_llm_request(error_type, elapsed_seconds)
                logger.warning(
                    "LLM summary request failed "
                    "(chat_id=%s, model=%s, attempt=%s/%s, elapsed=%.2fs, error_type=%s, error=%s)",
                    chat_id,
                    model_name,
                    attempt,
                    max_attempts,
                    elapsed_seconds,
                    type(exc).__name__,
                    error_summary,
                )
                if await should_retry_after_error(exc, attempt, delay, deadline=deadline):
                    continue
                break

    logger.error(
        "LLM summary generation failed after retries (chat_id=%s, model=%s, attempts=%s)",
        chat_id,
        model_name,
        max_attempts,
    )
    return None


def calculate_rate(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def get_llm_attempt_timeout_seconds(deadline: float | None = None) -> float:
    if deadline is None:
        return SUMMARY_TIMEOUT_SECONDS
    return min(SUMMARY_TIMEOUT_SECONDS, max(0.0, deadline - time.monotonic()))


async def sleep_before_retry(delay: int | float, deadline: float | None = None) -> bool:
    if delay <= 0:
        return True

    if deadline is not None:
        remaining_after_delay = deadline - time.monotonic() - delay
        if remaining_after_delay < SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS:
            logger.warning(
                "Skipping LLM retry because deadline would expire during backoff "
                "(delay=%s, remaining_after_delay=%.2fs, min_attempt_timeout=%.2fs)",
                delay,
                remaining_after_delay,
                SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS,
            )
            return False

    await asyncio.sleep(delay)
    return True


def build_chat_completion_kwargs(
    model_name: str,
    system_prompt: str,
    anonymized_text: str,
    max_output_tokens: int,
    provider: str | None = None,
    thinking_mode: str = "disabled",
) -> dict:
    request_kwargs = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Напиши ироничный пересказ этого лога:\n"
                    f"<chat_history>\n{anonymized_text}\n</chat_history>"
                ),
            },
        ],
        "temperature": 0.6,
        "presence_penalty": 0.8,
        "frequency_penalty": 0.3,
        "max_tokens": max_output_tokens,
    }
    if provider == "DeepSeek API":
        request_kwargs["extra_body"] = {"thinking": {"type": thinking_mode}}
    return request_kwargs


def build_chunk_chat_completion_kwargs(
    model_name: str,
    system_prompt: str,
    chunk_payload: dict,
    provider: str | None = None,
) -> dict:
    request_kwargs = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Сверни этот chunk строго в JSON без пояснений.\n"
                    f"<chunk_source>\n{json.dumps(chunk_payload, ensure_ascii=False)}\n</chunk_source>"
                ),
            },
        ],
        "temperature": 0.1,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "max_tokens": CHUNK_SUMMARY_MAX_OUTPUT_TOKENS,
    }
    if provider == "DeepSeek API":
        request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return request_kwargs


def get_reasoning_tokens(usage: object | None) -> int:
    if not usage:
        return 0
    details = getattr(usage, "completion_tokens_details", None)
    if not details:
        return 0
    return int(getattr(details, "reasoning_tokens", 0) or 0)


def classify_llm_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"

    error_message = str(exc).lower()
    if "429" in error_message or "rate limit" in error_message:
        return "rate_limited"

    return "error"


def summarize_exception_for_log(exc: Exception, max_chars: int = 240) -> str:
    message = str(exc).strip().replace("\n", " ")
    if not message:
        return "<empty>"
    if len(message) > max_chars:
        return f"{message[:max_chars]}..."
    return message


def build_system_prompt(
    base_prompt: str,
    turn_count: int,
    chat_id: int,
    dynamic_example: "SummaryExample | None" = None,
) -> str:
    prompt = inject_dynamic_example(base_prompt, dynamic_example)
    if turn_count < 10:
        logger.debug(
            "Applying short-log prompt override (chat_id=%s, turns=%s)",
            chat_id,
            turn_count,
        )
        prompt += (
            "\n\n[DYNAMIC OVERRIDE]: Лог очень короткий. Напиши 1-2 коротких предложения. "
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать сюжет, растекаться или писать много абзацев."
        )
    elif turn_count < 30:
        prompt += (
            "\n\n[DYNAMIC OVERRIDE]: Лог небольшой. Сделай компактный пересказ в 2-4 предложениях "
            "без длинных вступлений и без лишней воды."
        )

    salt = f"Chat:{chat_id}_Time:{time.time()}"
    return f"{prompt}\n\n[Request-Salt: {salt}]"


def inject_dynamic_example(base_prompt: str, dynamic_example: "SummaryExample | None") -> str:
    if dynamic_example is None:
        return base_prompt

    example_block = format_dynamic_example(dynamic_example)
    prompt, replacements = EXAMPLE_BLOCK_PATTERN.subn(example_block, base_prompt, count=1)
    if replacements:
        logger.info(
            "Dynamic summary example injected into prompt (summary_log_id=%s, mode=replace)",
            dynamic_example.summary_log_id,
        )
        return prompt

    logger.info(
        "Dynamic summary example injected into prompt (summary_log_id=%s, mode=append)",
        dynamic_example.summary_log_id,
    )
    return f"{base_prompt.rstrip()}\n\n{example_block}"


def format_dynamic_example(dynamic_example: "SummaryExample") -> str:
    return (
        "<example>\n"
        "<input_log>\n"
        f"{dynamic_example.input_log.strip()}\n"
        "</input_log>\n"
        "<ideal_summary>\n"
        f"{dynamic_example.ideal_summary.strip()}\n"
        "</ideal_summary>\n"
        "</example>"
    )


def get_summary_max_tokens(messages_count: int) -> int:
    if messages_count < 10:
        return SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS
    if messages_count < 30:
        return SUMMARY_MEDIUM_LOG_MAX_OUTPUT_TOKENS
    return SUMMARY_MAX_OUTPUT_TOKENS


async def should_retry_after_error(
    exc: Exception,
    attempt: int,
    delay: int,
    deadline: float | None = None,
) -> bool:
    max_attempts = len(SUMMARY_RETRY_BACKOFF_SECONDS)
    error_message = str(exc).lower()

    if isinstance(exc, asyncio.TimeoutError):
        logger.warning(
            "Таймаут (%sс)! Попытка %s из %s.",
            int(SUMMARY_TIMEOUT_SECONDS),
            attempt,
            max_attempts,
        )
        if attempt < max_attempts:
            logger.info("Retrying LLM request after timeout in %s seconds.", delay)
            return await sleep_before_retry(delay, deadline)
        logger.error("Сдаемся. Модель стабильно отваливается по таймауту.")
        return False

    if "429" in error_message or "rate limit" in error_message:
        logger.warning("Поймали 429. Попытка %s из %s. Ждем %s сек...", attempt, max_attempts, delay)
        if attempt < max_attempts:
            logger.info("Retrying LLM request after rate limit in %s seconds.", delay)
            return await sleep_before_retry(delay, deadline)
        logger.error("Сдаемся. API не пускает даже после долгих пауз.")
        return False

    logger.warning("Другая ошибка модели: %s", exc)
    return False


def strip_thinking_tags(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    match = JSON_CODE_FENCE_PATTERN.fullmatch(stripped)
    if not match:
        return stripped
    return match.group("body").strip()


def prepare_final_message(
    public_summary: str,
    anonymizer: Anonymizer,
    experiment_notice: str = "",
) -> str:
    logger.info("Preparing final Telegram message (raw_summary_chars=%s)", len(public_summary))
    public_summary = strip_role_tags_from_summary(public_summary)
    public_summary = anonymizer.decode(public_summary)
    logger.info("Summary decoded (decoded_summary_chars=%s)", len(public_summary))
    experiment_notice = experiment_notice.strip()

    if len(public_summary) > 3000:
        public_summary = public_summary[:3000] + "...\n\n[Текст обрезан]"
        logger.info("Summary soft cut applied (limit_chars=3000)")

    footer = build_final_message_footer(experiment_notice)
    final_text = html.escape(public_summary) + footer
    logger.info("Final Telegram message prepared (html_chars=%s)", len(final_text))

    if len(final_text) > 4090:
        logger.warning("Message still too long (%s), applying emergency cut...", len(final_text))
        public_summary = public_summary[:1500] + "...\n\n[Emergency cut]"
        final_text = html.escape(public_summary) + footer
        logger.info("Emergency-cut Telegram message prepared (html_chars=%s)", len(final_text))

    return final_text


def build_final_message_footer(experiment_notice: str) -> str:
    footer_lines = ["\n\n—"]
    if experiment_notice:
        footer_lines.append(html.escape(experiment_notice))
    footer_lines.append("<i>Это сообщение сгенерировано ИИ и может быть неточным.</i>")
    return "\n".join(footer_lines)


def strip_role_tags_from_summary(text: str) -> str:
    return ROLE_TAG_LEAK_PATTERN.sub(r"\1", text)
