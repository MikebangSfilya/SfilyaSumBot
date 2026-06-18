import time
from contextlib import contextmanager

from prometheus_client import Counter, Histogram, start_http_server


SUMMARY_REQUESTS_TOTAL = Counter(
    "sumbot_summary_requests_total",
    "Total /summary requests grouped by result.",
    ("result",),
)

SUMMARY_DURATION_SECONDS = Histogram(
    "sumbot_summary_duration_seconds",
    "Duration of /summary processing in seconds.",
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89),
)

SUMMARY_SOURCE_MESSAGES = Histogram(
    "sumbot_summary_source_messages",
    "Number of raw source messages used for /summary requests.",
    buckets=(1, 5, 10, 25, 50, 100, 200, 400, 800),
)

SUMMARY_CONTEXT_TURNS = Histogram(
    "sumbot_summary_context_turns",
    "Number of rendered turns passed to the LLM per summary request.",
    buckets=(1, 5, 10, 25, 50, 100, 200, 400),
)

SUMMARY_CONTEXT_MERGED_MESSAGES = Histogram(
    "sumbot_summary_context_merged_messages",
    "Number of merged source messages in prepared summary context.",
    buckets=(0, 1, 2, 5, 10, 20, 40, 80, 160),
)

LLM_REQUESTS_TOTAL = Counter(
    "sumbot_llm_requests_total",
    "Total LLM summary generation attempts grouped by result.",
    ("result",),
)

LLM_REQUEST_DURATION_SECONDS = Histogram(
    "sumbot_llm_request_duration_seconds",
    "Duration of LLM summary generation attempts in seconds.",
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89),
)

LLM_TOKENS_TOTAL = Counter(
    "sumbot_llm_tokens_total",
    "Total LLM tokens consumed grouped by direction.",
    ("type",),
)

TELEGRAM_UPDATES_TOTAL = Counter(
    "sumbot_telegram_updates_total",
    "Total incoming Telegram updates handled by catch-all handler.",
)

FEEDBACK_TOTAL = Counter(
    "sumbot_feedback_total",
    "Total feedback callbacks grouped by value.",
    ("value",),
)


def start_metrics_server(host: str, port: int) -> None:
    start_http_server(port=port, addr=host)


def mark_summary_result(result: str) -> None:
    SUMMARY_REQUESTS_TOTAL.labels(result=result).inc()


def observe_summary_context(raw_message_count: int, turn_count: int, merged_count: int) -> None:
    SUMMARY_SOURCE_MESSAGES.observe(raw_message_count)
    SUMMARY_CONTEXT_TURNS.observe(turn_count)
    SUMMARY_CONTEXT_MERGED_MESSAGES.observe(merged_count)


def observe_llm_request(
    result: str,
    duration_seconds: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    LLM_REQUESTS_TOTAL.labels(result=result).inc()
    LLM_REQUEST_DURATION_SECONDS.observe(duration_seconds)
    if input_tokens:
        LLM_TOKENS_TOTAL.labels(type="input").inc(input_tokens)
    if output_tokens:
        LLM_TOKENS_TOTAL.labels(type="output").inc(output_tokens)


def observe_feedback(value: str) -> None:
    FEEDBACK_TOTAL.labels(value=value).inc()


def inc_telegram_updates() -> None:
    TELEGRAM_UPDATES_TOTAL.inc()


@contextmanager
def summary_duration_timer():
    started_at = time.perf_counter()
    try:
        yield
    finally:
        SUMMARY_DURATION_SECONDS.observe(time.perf_counter() - started_at)
