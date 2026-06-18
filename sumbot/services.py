from dataclasses import dataclass, field
import logging

from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import config
from sumbot.chunks import build_chunking_enabled_key, build_chunking_enabled_pattern
from sumbot.constants import DEEPSEEK_BASE_URL, OPENROUTER_BASE_URL
from sumbot.chat_approval import (
    CHAT_APPROVAL_STATUS_SEEN,
    CHAT_APPROVAL_STATUSES,
    build_chat_approval_key,
)
from sumbot.prompt_builder import (
    DEFAULT_AGGRESSIVENESS,
    LEGACY_PRESENTATION_PRESETS,
    PresentationCatalog,
    PresentationOption,
    SummaryPresentationSettings,
    build_default_presentation_settings,
    build_prompt_profile_key,
    build_presentation_settings,
    build_summary_presentation_key,
    load_style_catalog,
    load_tone_catalog,
    parse_presentation_settings,
)

logger = logging.getLogger("SumBot.services")

MODEL_SELECTION_REDIS_KEY_PREFIX = "settings:llm_model_id"
SUMMARY_MAX_TOKENS_REDIS_KEY_PREFIX = "settings:summary_max_output_tokens"
SUMMARY_THINKING_MODE_REDIS_KEY_PREFIX = "settings:summary_thinking_mode"
DEFAULT_CHUNK_MODEL_ID = "deepseek:deepseek-v4-flash"
SHORT_LOG_MESSAGE_LIMIT = 50
SUMMARY_THINKING_MODE_DISABLED = "disabled"
SUMMARY_THINKING_MODE_ENABLED = "enabled"
SUMMARY_THINKING_MODE_OPTIONS = frozenset(
    {
        SUMMARY_THINKING_MODE_DISABLED,
        SUMMARY_THINKING_MODE_ENABLED,
    }
)


@dataclass(frozen=True, slots=True)
class LlmModelOption:
    model_id: str
    provider: str
    label: str
    model_name: str
    base_url: str
    api_key: str

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True, slots=True)
class ActiveLlmModel:
    option: LlmModelOption
    client: AsyncOpenAI


@dataclass(frozen=True, slots=True)
class SummaryGenerationSettings:
    max_output_tokens: int | None = None
    thinking_mode: str = SUMMARY_THINKING_MODE_DISABLED


@dataclass(slots=True)
class BotServices:
    redis: Redis
    llm_client: AsyncOpenAI
    model_name: str
    db_engine: AsyncEngine | None = None
    llm_clients: dict[str, AsyncOpenAI] = field(default_factory=dict)
    model_options: dict[str, LlmModelOption] = field(default_factory=dict)
    default_model_id: str = ""
    chunk_model_id: str = ""
    short_log_model_id: str = ""
    style_catalog: PresentationCatalog = field(default_factory=load_style_catalog)
    tone_catalog: PresentationCatalog = field(default_factory=load_tone_catalog)

    def __post_init__(self) -> None:
        if self.model_options and self.llm_clients and self.default_model_id:
            if not self.chunk_model_id:
                self.chunk_model_id = self.default_model_id
            return

        legacy_option = LlmModelOption(
            model_id="legacy",
            provider="OpenAI-compatible",
            label=self.model_name,
            model_name=self.model_name,
            base_url=OPENROUTER_BASE_URL,
            api_key="",
        )
        self.model_options = {"legacy": legacy_option}
        self.llm_clients = {"legacy": self.llm_client}
        self.default_model_id = "legacy"
        self.chunk_model_id = "legacy"
        self.short_log_model_id = ""

    def list_model_options(self) -> tuple[LlmModelOption, ...]:
        return tuple(self.model_options.values())

    async def get_active_llm_model(self, chat_id: int) -> ActiveLlmModel:
        model_id = await self._get_selected_model_id(chat_id)
        option = self.model_options.get(model_id) or self.model_options[self.default_model_id]
        client = self.llm_clients[option.model_id]
        return ActiveLlmModel(option=option, client=client)

    async def get_llm_fallback_chain(
        self,
        chat_id: int,
        *,
        source_message_count: int | None = None,
    ) -> tuple[ActiveLlmModel, ...]:
        selected = await self.get_active_llm_model(chat_id)
        chain: list[ActiveLlmModel] = []
        seen_model_ids: set[str] = set()

        def append_if_available(option: LlmModelOption | None) -> None:
            if option is None or not option.is_available or option.model_id in seen_model_ids:
                return
            chain.append(ActiveLlmModel(option=option, client=self.llm_clients[option.model_id]))
            seen_model_ids.add(option.model_id)

        short_log_eligible = source_message_count is not None and source_message_count <= SHORT_LOG_MESSAGE_LIMIT
        if short_log_eligible:
            append_if_available(self.model_options.get(self.short_log_model_id))

        append_if_available(selected.option)
        append_if_available(self.model_options.get(self.default_model_id))

        for option in self.model_options.values():
            if option.model_id == self.short_log_model_id and not short_log_eligible:
                continue
            append_if_available(option)

        return tuple(chain)

    async def get_chunk_llm_model(self) -> ActiveLlmModel:
        option = self.model_options[self.chunk_model_id]
        return ActiveLlmModel(option=option, client=self.llm_clients[option.model_id])

    async def set_active_llm_model(self, chat_id: int, model_id: str) -> ActiveLlmModel:
        option = self.model_options.get(model_id)
        if option is None:
            raise KeyError(model_id)
        if not option.is_available:
            raise ValueError(model_id)

        await self.redis.set(build_model_selection_key(chat_id), model_id)
        logger.info(
            "Active LLM model changed for chat (chat_id=%s, model_id=%s, provider=%s, model=%s)",
            chat_id,
            option.model_id,
            option.provider,
            option.model_name,
        )
        return ActiveLlmModel(option=option, client=self.llm_clients[option.model_id])

    async def get_summary_generation_settings(self, chat_id: int) -> SummaryGenerationSettings:
        raw_max_tokens = await self.redis.get(build_summary_max_tokens_key(chat_id))
        raw_thinking_mode = await self.redis.get(build_summary_thinking_mode_key(chat_id))
        if isinstance(raw_max_tokens, bytes):
            raw_max_tokens = raw_max_tokens.decode()
        if isinstance(raw_thinking_mode, bytes):
            raw_thinking_mode = raw_thinking_mode.decode()

        max_output_tokens: int | None = None
        if raw_max_tokens:
            try:
                parsed_max_tokens = int(raw_max_tokens)
            except (TypeError, ValueError):
                logger.warning(
                    "Saved summary max tokens value is invalid; using dynamic default "
                    "(chat_id=%s, value=%r)",
                    chat_id,
                    raw_max_tokens,
                )
            else:
                if parsed_max_tokens > 0:
                    max_output_tokens = parsed_max_tokens
                else:
                    logger.warning(
                        "Saved summary max tokens value is not positive; using dynamic default "
                        "(chat_id=%s, value=%r)",
                        chat_id,
                        raw_max_tokens,
                    )

        thinking_mode = (
            raw_thinking_mode
            if isinstance(raw_thinking_mode, str) and raw_thinking_mode in SUMMARY_THINKING_MODE_OPTIONS
            else SUMMARY_THINKING_MODE_DISABLED
        )
        return SummaryGenerationSettings(max_output_tokens=max_output_tokens, thinking_mode=thinking_mode)

    async def is_chunking_enabled(self, chat_id: int) -> bool:
        raw_value = await self.redis.get(build_chunking_enabled_key(chat_id))
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode()
        if not isinstance(raw_value, str):
            return False
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}

    async def set_chunking_enabled(self, chat_id: int, enabled: bool) -> bool:
        if enabled:
            await self.redis.set(build_chunking_enabled_key(chat_id), "1")
        else:
            await self.redis.delete(build_chunking_enabled_key(chat_id))

        saved_enabled = await self.is_chunking_enabled(chat_id)
        logger.info(
            "Chunking flag changed for chat (chat_id=%s, enabled=%s)",
            chat_id,
            saved_enabled,
        )
        return saved_enabled

    async def count_chunking_enabled_chats(self) -> int:
        count = 0
        async for _ in self.redis.scan_iter(match=build_chunking_enabled_pattern()):
            count += 1
        return count

    async def set_summary_max_output_tokens(
        self,
        chat_id: int,
        max_output_tokens: int | None,
    ) -> SummaryGenerationSettings:
        if max_output_tokens is None:
            await self.redis.delete(build_summary_max_tokens_key(chat_id))
        elif max_output_tokens <= 0:
            raise ValueError(max_output_tokens)
        else:
            await self.redis.set(build_summary_max_tokens_key(chat_id), str(max_output_tokens))

        settings = await self.get_summary_generation_settings(chat_id)
        logger.info(
            "Summary max output tokens changed for chat (chat_id=%s, max_output_tokens=%s)",
            chat_id,
            settings.max_output_tokens,
        )
        return settings

    async def set_summary_thinking_mode(self, chat_id: int, thinking_mode: str) -> SummaryGenerationSettings:
        if thinking_mode not in SUMMARY_THINKING_MODE_OPTIONS:
            raise ValueError(thinking_mode)

        await self.redis.set(build_summary_thinking_mode_key(chat_id), thinking_mode)
        settings = await self.get_summary_generation_settings(chat_id)
        logger.info(
            "Summary thinking mode changed for chat (chat_id=%s, thinking_mode=%s)",
            chat_id,
            settings.thinking_mode,
        )
        return settings

    def list_summary_styles(self) -> tuple[PresentationOption, ...]:
        return self.style_catalog.options

    def list_summary_tones(self) -> tuple[PresentationOption, ...]:
        return self.tone_catalog.options

    async def get_summary_presentation_settings(self, chat_id: int) -> SummaryPresentationSettings:
        raw_settings = await self.redis.get(build_summary_presentation_key(chat_id))
        if isinstance(raw_settings, bytes):
            raw_settings = raw_settings.decode()
        if isinstance(raw_settings, str):
            try:
                return parse_presentation_settings(raw_settings, self.style_catalog, self.tone_catalog)
            except ValueError:
                logger.warning(
                    "Saved summary presentation settings are invalid; checking legacy/default "
                    "(chat_id=%s, value=%r)",
                    chat_id,
                    raw_settings,
                )

        raw_legacy_style = await self.redis.get(build_prompt_profile_key(chat_id))
        if isinstance(raw_legacy_style, bytes):
            raw_legacy_style = raw_legacy_style.decode()
        if isinstance(raw_legacy_style, str) and raw_legacy_style in self.style_catalog.by_id:
            tone_id, aggressiveness = LEGACY_PRESENTATION_PRESETS.get(
                raw_legacy_style,
                (self.tone_catalog.default_option_id, DEFAULT_AGGRESSIVENESS),
            )
            return build_presentation_settings(
                self.style_catalog,
                self.tone_catalog,
                style_id=raw_legacy_style,
                tone_id=tone_id,
                aggressiveness=aggressiveness,
            )
        return build_default_presentation_settings(self.style_catalog, self.tone_catalog)

    async def set_summary_presentation_settings(
        self,
        chat_id: int,
        *,
        style_id: str | None = None,
        tone_id: str | None = None,
        aggressiveness: int | None = None,
    ) -> SummaryPresentationSettings:
        current = await self.get_summary_presentation_settings(chat_id)
        updated = build_presentation_settings(
            self.style_catalog,
            self.tone_catalog,
            style_id=style_id or current.style.option_id,
            tone_id=tone_id or current.tone.option_id,
            aggressiveness=(
                aggressiveness if aggressiveness is not None else current.aggressiveness.level
            ),
        )
        await self.redis.set(build_summary_presentation_key(chat_id), updated.to_json())
        await self.redis.delete(build_prompt_profile_key(chat_id))
        logger.info(
            "Summary presentation changed "
            "(chat_id=%s, style=%s, tone=%s, aggressiveness=%s)",
            chat_id,
            updated.style.option_id,
            updated.tone.option_id,
            updated.aggressiveness.level,
        )
        return updated

    async def reset_summary_presentation_settings(self, chat_id: int) -> SummaryPresentationSettings:
        await self.redis.delete(build_summary_presentation_key(chat_id))
        await self.redis.delete(build_prompt_profile_key(chat_id))
        settings = build_default_presentation_settings(self.style_catalog, self.tone_catalog)
        logger.info("Summary presentation reset (chat_id=%s)", chat_id)
        return settings

    async def get_chat_approval_status(self, chat_id: int) -> str:
        saved_status = await self.get_saved_chat_approval_status(chat_id)
        return saved_status or CHAT_APPROVAL_STATUS_SEEN

    async def get_saved_chat_approval_status(self, chat_id: int) -> str | None:
        raw_status = await self.redis.get(build_chat_approval_key(chat_id))
        if isinstance(raw_status, bytes):
            raw_status = raw_status.decode()
        if isinstance(raw_status, str) and raw_status in CHAT_APPROVAL_STATUSES:
            return raw_status
        return None

    async def set_chat_approval_status(self, chat_id: int, status: str) -> str:
        if status not in CHAT_APPROVAL_STATUSES:
            raise ValueError(status)
        await self.redis.set(build_chat_approval_key(chat_id), status)
        logger.info("Chat approval status changed (chat_id=%s, status=%s)", chat_id, status)
        return status

    async def _get_selected_model_id(self, chat_id: int) -> str:
        raw_model_id = await self.redis.get(build_model_selection_key(chat_id))
        if isinstance(raw_model_id, bytes):
            raw_model_id = raw_model_id.decode()
        if (
            isinstance(raw_model_id, str)
            and raw_model_id in self.model_options
            and self.model_options[raw_model_id].is_available
        ):
            return raw_model_id
        if isinstance(raw_model_id, str) and raw_model_id in self.model_options:
            logger.warning(
                "Saved chat LLM model is unavailable because API key is empty; falling back "
                "(chat_id=%s, model_id=%s)",
                chat_id,
                raw_model_id,
            )
        if self.model_options[self.default_model_id].is_available:
            return self.default_model_id
        for model_id, option in self.model_options.items():
            if option.is_available:
                return model_id
        return self.default_model_id

    async def close(self) -> None:
        logger.info("Closing bot services (db_enabled=%s)", self.db_engine is not None)
        try:
            await self.redis.aclose()
            logger.info("Redis connection closed.")
        finally:
            if self.db_engine:
                await self.db_engine.dispose()
                logger.info("Database engine disposed.")


def create_services() -> BotServices:
    model_options = build_llm_model_options()
    default_model_id = resolve_default_model_id(model_options)
    chunk_model_id = resolve_chunk_model_id(model_options)
    short_log_model_id = resolve_short_log_model_id(model_options)
    configured_chunk_model_id = normalize_configured_chunk_model_id(config.CHUNK_SUMMARY_MODEL_ID)
    default_option = model_options[default_model_id]
    chunk_option = model_options[chunk_model_id]
    logger.info(
        "Creating services "
        "(redis_host=%s, db_enabled=%s, default_provider=%s, default_model=%s, "
        "default_base_url=%s, chunk_provider=%s, chunk_model=%s, env_file=%s, experiment_notice_enabled=%s, "
        "experiment_notice_chars=%s, dynamic_examples_enabled=%s)",
        config.REDIS_HOST,
        bool(config.db_url),
        default_option.provider,
        default_option.model_name,
        default_option.base_url,
        chunk_option.provider,
        chunk_option.model_name,
        config.ENV_FILE,
        bool(config.SUMMARY_EXPERIMENT_NOTICE),
        len(config.SUMMARY_EXPERIMENT_NOTICE),
        config.SUMMARY_DYNAMIC_EXAMPLES_ENABLED,
    )
    if short_log_model_id:
        short_option = model_options[short_log_model_id]
        logger.info(
            "Short-log canary enabled (limit_messages=%s, model_id=%s, provider=%s, model=%s)",
            SHORT_LOG_MESSAGE_LIMIT,
            short_option.model_id,
            short_option.provider,
            short_option.model_name,
        )
    if configured_chunk_model_id and chunk_model_id != configured_chunk_model_id:
        logger.warning(
            "Configured chunk model is unavailable; using fallback "
            "(configured_model_id=%s, fallback_model_id=%s)",
            configured_chunk_model_id,
            chunk_model_id,
        )
    if not any(option.is_available for option in model_options.values()):
        logger.warning("No LLM API keys configured; summary generation will fail until a key is set.")
    for option in model_options.values():
        if not option.is_available:
            logger.warning(
                "LLM model option is unavailable because API key is empty "
                "(model_id=%s, provider=%s, model=%s)",
                option.model_id,
                option.provider,
                option.model_name,
            )

    db_engine = create_async_engine(config.db_url, echo=False) if config.db_url else None
    redis_client = Redis(host=config.REDIS_HOST, port=6379, decode_responses=True)
    llm_clients = {
        option.model_id: AsyncOpenAI(api_key=option.api_key or "missing-api-key", base_url=option.base_url)
        for option in model_options.values()
    }

    return BotServices(
        redis=redis_client,
        llm_client=llm_clients[default_model_id],
        model_name=default_option.model_name,
        db_engine=db_engine,
        llm_clients=llm_clients,
        model_options=model_options,
        default_model_id=default_model_id,
        chunk_model_id=chunk_model_id,
        short_log_model_id=short_log_model_id,
    )


def build_llm_model_options() -> dict[str, LlmModelOption]:
    options: dict[str, LlmModelOption] = {}
    for openrouter_model in config.OPENROUTER_MODELS:
        add_openrouter_model_option(options, openrouter_model)

    configured_chunk_model_id = normalize_configured_chunk_model_id(config.CHUNK_SUMMARY_MODEL_ID)
    if configured_chunk_model_id.startswith("openrouter:") and configured_chunk_model_id not in options:
        chunk_model_name = configured_chunk_model_id.removeprefix("openrouter:")
        add_openrouter_model_option(options, chunk_model_name)

    configured_short_log_model_id = normalize_configured_openrouter_or_prefixed_model_id(config.SHORT_LOG_MODEL_ID)
    if configured_short_log_model_id.startswith("openrouter:") and configured_short_log_model_id not in options:
        short_log_model_name = configured_short_log_model_id.removeprefix("openrouter:")
        add_openrouter_model_option(options, short_log_model_name)

    for model_name in config.DEEPSEEK_MODELS:
        model_id = f"deepseek:{model_name}"
        options[model_id] = LlmModelOption(
            model_id=model_id,
            provider="DeepSeek API",
            label=f"DeepSeek API · {model_name}",
            model_name=model_name,
            base_url=DEEPSEEK_BASE_URL,
            api_key=config.DEEPSEEK_API_KEY,
        )

    if DEFAULT_CHUNK_MODEL_ID not in options:
        options[DEFAULT_CHUNK_MODEL_ID] = LlmModelOption(
            model_id=DEFAULT_CHUNK_MODEL_ID,
            provider="DeepSeek API",
            label="DeepSeek API · deepseek-v4-flash",
            model_name="deepseek-v4-flash",
            base_url=DEEPSEEK_BASE_URL,
            api_key=config.DEEPSEEK_API_KEY,
        )

    return options


def add_openrouter_model_option(options: dict[str, LlmModelOption], model_name: str) -> None:
    model_id = f"openrouter:{model_name}"
    options[model_id] = LlmModelOption(
        model_id=model_id,
        provider="OpenRouter",
        label=f"OpenRouter · {model_name}",
        model_name=model_name,
        base_url=OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
    )


def build_model_selection_key(chat_id: int) -> str:
    return f"{MODEL_SELECTION_REDIS_KEY_PREFIX}:{chat_id}"


def build_summary_max_tokens_key(chat_id: int) -> str:
    return f"{SUMMARY_MAX_TOKENS_REDIS_KEY_PREFIX}:{chat_id}"


def build_summary_thinking_mode_key(chat_id: int) -> str:
    return f"{SUMMARY_THINKING_MODE_REDIS_KEY_PREFIX}:{chat_id}"


def resolve_default_model_id(model_options: dict[str, LlmModelOption]) -> str:
    if config.LLM_DEFAULT_MODEL_ID in model_options:
        return config.LLM_DEFAULT_MODEL_ID

    for model_id, option in model_options.items():
        if option.is_available:
            return model_id

    return next(iter(model_options))


def resolve_chunk_model_id(model_options: dict[str, LlmModelOption]) -> str:
    configured_model_id = normalize_configured_chunk_model_id(config.CHUNK_SUMMARY_MODEL_ID)
    if configured_model_id in model_options and model_options[configured_model_id].is_available:
        return configured_model_id

    direct_option = model_options.get(DEFAULT_CHUNK_MODEL_ID)
    if direct_option is not None and direct_option.is_available:
        return DEFAULT_CHUNK_MODEL_ID

    openrouter_option = model_options.get("openrouter:deepseek/deepseek-v4-flash")
    if openrouter_option is not None and openrouter_option.is_available:
        return "openrouter:deepseek/deepseek-v4-flash"

    return resolve_default_model_id(model_options)


def resolve_short_log_model_id(model_options: dict[str, LlmModelOption]) -> str:
    configured_model_id = normalize_configured_openrouter_or_prefixed_model_id(config.SHORT_LOG_MODEL_ID)
    if not configured_model_id:
        return ""
    if ":free" in configured_model_id:
        logger.warning("Free OpenRouter models are not allowed for short-log canary (model_id=%s)", configured_model_id)
        return ""
    if configured_model_id in model_options and model_options[configured_model_id].is_available:
        return configured_model_id
    logger.warning("Configured short-log model is unavailable; canary disabled (model_id=%s)", configured_model_id)
    return ""


def normalize_configured_chunk_model_id(model_id: str) -> str:
    return normalize_configured_openrouter_or_prefixed_model_id(model_id)


def normalize_configured_openrouter_or_prefixed_model_id(model_id: str) -> str:
    normalized = model_id.strip()
    if not normalized or normalized.startswith(("openrouter:", "deepseek:")):
        return normalized
    if "/" in normalized:
        return f"openrouter:{normalized}"
    return normalized
