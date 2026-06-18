import os

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

DEFAULT_SUMMARY_PERIOD_SECONDS = 24 * 3600


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return float(value)


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return int(value)


def _get_int_set_env(name: str, default: frozenset[int]) -> frozenset[int]:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    normalized = value.replace(",", " ")
    return frozenset(int(item) for item in normalized.split())


RATE_LIMIT_SECONDS = 60
RATE_LIMIT_EXEMPT_USER_IDS = _get_int_set_env("RATE_LIMIT_EXEMPT_USER_IDS", frozenset())
FEEDBACK_DETAILS_DELETE_DELAY_SECONDS = 7
FEEDBACK_DETAILS_PROMPT_TIMEOUT_SECONDS = 60
SUMMARY_FEEDBACK_RATE_LIMIT_SECONDS = 5
SUMMARY_FEEDBACK_KEYBOARD_TTL_SECONDS = 24 * 3600
SUMMARY_TIMEOUT_SECONDS = _get_float_env("SUMMARY_TIMEOUT_SECONDS", 75.0)
SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS = _get_float_env("SUMMARY_FALLBACK_TOTAL_TIMEOUT_SECONDS", 180.0)
SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS = _get_float_env("SUMMARY_MIN_LLM_ATTEMPT_TIMEOUT_SECONDS", 5.0)
SUMMARY_MAX_OUTPUT_TOKENS = 1200
SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS = 180
SUMMARY_MEDIUM_LOG_MAX_OUTPUT_TOKENS = 450
SUMMARY_RETRY_BACKOFF_SECONDS = (5, 15, 30, 0)
CHUNK_SUMMARY_TIMEOUT_SECONDS = _get_float_env("CHUNK_SUMMARY_TIMEOUT_SECONDS", 30.0)
CHUNK_SUMMARY_RETRY_BACKOFF_SECONDS = (1, 3, 0)
CHUNK_SUMMARY_MAX_OUTPUT_TOKENS = 900
CHUNK_MESSAGE_LIMIT = 50
CHUNK_SUMMARY_RETENTION_LIMIT = 10
CHUNK_SUMMARY_MAX_ATTEMPTS = 3
CHUNK_WORKER_POLL_TIMEOUT_SECONDS = _get_float_env("CHUNK_WORKER_POLL_TIMEOUT_SECONDS", 5.0)
SUMMARY_LOG_COUNTER_NAME = "summary_logs_written"
SUMMARY_DYNAMIC_EXAMPLE_MAX_CONTEXT_CHARS = 4000
SUMMARY_DYNAMIC_EXAMPLE_MAX_RESPONSE_CHARS = 2500

DEBUG_USER_ID = _get_int_env("DEBUG_USER_ID", 0)
ANALYTICS_CHAT_ID = _get_int_env("ANALYTICS_CHAT_ID", DEBUG_USER_ID)
SUMMARY_LOG_RETENTION_LIMIT = _get_int_env("SUMMARY_LOG_RETENTION_LIMIT", 200)
CHAT_REMINDER_COOLDOWN_SECONDS = _get_int_env("CHAT_REMINDER_COOLDOWN_SECONDS", 7 * 24 * 3600)
CHAT_REMINDER_EXCLUDED_CHAT_IDS = _get_int_set_env(
    "CHAT_REMINDER_EXCLUDED_CHAT_IDS",
    frozenset(),
)

HISTORY_SKIP_MARKERS = ("/summary", "/debug", "⏳")
