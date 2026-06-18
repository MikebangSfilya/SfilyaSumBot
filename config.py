import os
from enum import IntEnum

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False

# --- КОНФИГУРАЦИЯ ---
ENV_FILE = os.getenv("SUMBOT_ENV_FILE", ".env")
load_dotenv(ENV_FILE, override=bool(os.getenv("SUMBOT_ENV_FILE")))

db_url = os.getenv("DATABASE_URL")


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default

class ChatConfig(IntEnum):
    HISTORY_LIMIT = 500  # Оставляем 500, как ты и хотел
    SESSION_PAUSE_SEC = 1800
    SHORT_DELAY_SEC = 15
    AGGREGATE_WINDOW_SEC = 60
    MIN_MESSAGE_LEN = 2

WELCOME_TEXT = (
    "👋 **Привет! Я summaryBot.**\n\n"
    "Я начинаю запоминать новые текстовые сообщения после добавления и не вижу старую историю Telegram.\n"
    "Когда накопится достаточно сообщений, я один раз подскажу, что можно вызвать /summary.\n\n"
    "Обычный пересказ: /summary\n"
    "Последние 100 сообщений: /summary 100\n"
    "Ежедневный дайджест включен по умолчанию. Администратор может отключить его: /digest off\n\n"
    "Перед отправкой в ИИ данные **анонимизируются**."
)

TG_TOKEN = os.getenv("TG_TOKEN")
API_KEY = os.getenv("LLM_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip() or API_KEY
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
SUMMARY_EXPERIMENT_NOTICE = os.getenv("SUMMARY_EXPERIMENT_NOTICE", "").strip()
SUMMARY_CONTEXT_V2_ENABLED = _get_bool_env("SUMMARY_CONTEXT_V2_ENABLED", False)
SUMMARY_DYNAMIC_EXAMPLES_ENABLED = _get_bool_env("SUMMARY_DYNAMIC_EXAMPLES_ENABLED", False)
METRICS_ENABLED = _get_bool_env("METRICS_ENABLED", True)
METRICS_HOST = os.getenv("METRICS_HOST", "0.0.0.0").strip() or "0.0.0.0"
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))
TRACING_ENABLED = _get_bool_env("TRACING_ENABLED", False)
TRACING_SERVICE_NAME = os.getenv("TRACING_SERVICE_NAME", "sumbot").strip() or "sumbot"
TRACING_OTLP_ENDPOINT = (
    os.getenv("TRACING_OTLP_ENDPOINT", "http://jaeger:4318/v1/traces").strip()
    or "http://jaeger:4318/v1/traces"
)
TRACING_SAMPLE_RATIO = min(max(_get_float_env("TRACING_SAMPLE_RATIO", 1.0), 0.0), 1.0)
DAILY_DIGEST_ENABLED = _get_bool_env("DAILY_DIGEST_ENABLED", False)
DAILY_DIGEST_DEFAULT_ENABLED = _get_bool_env("DAILY_DIGEST_DEFAULT_ENABLED", False)
DAILY_DIGEST_TIME = os.getenv("DAILY_DIGEST_TIME", "20:00").strip() or "20:00"
DAILY_DIGEST_TIMEZONE = os.getenv("DAILY_DIGEST_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
DAILY_DIGEST_JITTER_SECONDS = max(_get_int_env("DAILY_DIGEST_JITTER_SECONDS", 30), 0)
DAILY_DIGEST_MANUAL_SUPPRESSION_SECONDS = _get_int_env(
    "DAILY_DIGEST_MANUAL_SUPPRESSION_SECONDS",
    3600,
)
ONBOARDING_READY_MESSAGE_COUNT = _get_int_env("ONBOARDING_READY_MESSAGE_COUNT", 10)
ONBOARDING_PENDING_TTL_SECONDS = _get_int_env("ONBOARDING_PENDING_TTL_SECONDS", 7 * 24 * 3600)
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash").strip()
OPENROUTER_MODELS = list(
    dict.fromkeys(
        model
        for model in [
            OPENROUTER_MODEL,
            *os.getenv("OPENROUTER_MODELS", "").replace(",", " ").split(),
        ]
        if model
    )
)
DEEPSEEK_MODELS = [
    model.strip()
    for model in os.getenv("DEEPSEEK_MODELS", "deepseek-v4-flash,deepseek-v4-pro").replace(",", " ").split()
    if model.strip()
]
LLM_DEFAULT_MODEL_ID = os.getenv("LLM_DEFAULT_MODEL_ID", "").strip()
CHUNK_SUMMARY_MODEL_ID = os.getenv("CHUNK_SUMMARY_MODEL_ID", "").strip()
SHORT_LOG_MODEL_ID = os.getenv("SHORT_LOG_MODEL_ID", "").strip()

MODELS = [
    *OPENROUTER_MODELS,
]
