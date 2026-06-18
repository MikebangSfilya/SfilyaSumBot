import pytest

import sumbot.services as services_module
from sumbot.chunks import build_chunking_enabled_key
from sumbot.constants import DEEPSEEK_BASE_URL, OPENROUTER_BASE_URL
from sumbot.chat_approval import (
    CHAT_APPROVAL_STATUS_LEFT,
    CHAT_APPROVAL_STATUS_REVIEWED,
    CHAT_APPROVAL_STATUS_SEEN,
    build_chat_approval_key,
)
from sumbot.prompt_builder import build_prompt_profile_key, build_summary_presentation_key
from sumbot.services import (
    SUMMARY_THINKING_MODE_ENABLED,
    BotServices,
    build_model_selection_key,
    build_summary_max_tokens_key,
    build_summary_thinking_mode_key,
    create_services,
)


class FakeRedis:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False
        self.storage = {}

    async def aclose(self):
        self.closed = True

    async def get(self, key):
        return self.storage.get(key)

    async def set(self, key, value, **kwargs):
        self.storage[key] = value
        return True

    async def delete(self, key):
        self.storage.pop(key, None)

    async def scan_iter(self, match=None):
        prefix = (match or "").removesuffix("*")
        for key in list(self.storage):
            if match is None or str(key).startswith(prefix):
                yield key


class FakeLlmClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class FakeDbEngine:
    def __init__(self):
        self.disposed = False

    async def dispose(self):
        self.disposed = True


@pytest.mark.asyncio
async def test_bot_services_close_closes_redis_and_db_engine():
    redis = FakeRedis()
    db_engine = FakeDbEngine()
    services = BotServices(
        redis=redis,
        llm_client=FakeLlmClient(),
        model_name="model",
        db_engine=db_engine,
    )

    await services.close()

    assert redis.closed is True
    assert db_engine.disposed is True


def test_create_services_uses_configured_clients(monkeypatch):
    db_engine = FakeDbEngine()
    created = {}

    def fake_create_async_engine(db_url, echo=False):
        created["db"] = {"db_url": db_url, "echo": echo}
        return db_engine

    monkeypatch.setattr(services_module.config, "db_url", "postgresql+asyncpg://db")
    monkeypatch.setattr(services_module.config, "REDIS_HOST", "redis-host")
    monkeypatch.setattr(services_module.config, "OPENROUTER_API_KEY", "api-key")
    monkeypatch.setattr(services_module.config, "DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setattr(services_module.config, "OPENROUTER_MODEL", "model-a")
    monkeypatch.setattr(services_module.config, "OPENROUTER_MODELS", ["model-a"])
    monkeypatch.setattr(services_module.config, "DEEPSEEK_MODELS", ["deepseek-v4-flash"])
    monkeypatch.setattr(services_module.config, "LLM_DEFAULT_MODEL_ID", "openrouter:model-a")
    monkeypatch.setattr(services_module.config, "CHUNK_SUMMARY_MODEL_ID", "google/gemma-4-26b-a4b-it")
    monkeypatch.setattr(services_module, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(services_module, "Redis", FakeRedis)
    monkeypatch.setattr(services_module, "AsyncOpenAI", FakeLlmClient)

    services = create_services()

    assert services.redis.kwargs == {
        "host": "redis-host",
        "port": 6379,
        "decode_responses": True,
    }
    assert services.llm_client.kwargs == {
        "api_key": "api-key",
        "base_url": OPENROUTER_BASE_URL,
    }
    assert services.model_name == "model-a"
    assert services.default_model_id == "openrouter:model-a"
    assert services.chunk_model_id == "openrouter:google/gemma-4-26b-a4b-it"
    assert services.model_options[services.chunk_model_id].model_name == "google/gemma-4-26b-a4b-it"
    assert services.llm_clients[services.chunk_model_id].kwargs == {
        "api_key": "api-key",
        "base_url": OPENROUTER_BASE_URL,
    }
    assert services.llm_clients["deepseek:deepseek-v4-flash"].kwargs == {
        "api_key": "deepseek-key",
        "base_url": DEEPSEEK_BASE_URL,
    }
    assert services.db_engine is db_engine
    assert created["db"] == {"db_url": "postgresql+asyncpg://db", "echo": False}


def test_create_services_keeps_unavailable_models_without_crashing(monkeypatch):
    monkeypatch.setattr(services_module.config, "db_url", None)
    monkeypatch.setattr(services_module.config, "REDIS_HOST", "redis-host")
    monkeypatch.setattr(services_module.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(services_module.config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(services_module.config, "OPENROUTER_MODEL", "model-a")
    monkeypatch.setattr(services_module.config, "OPENROUTER_MODELS", ["model-a"])
    monkeypatch.setattr(services_module.config, "DEEPSEEK_MODELS", ["deepseek-v4-flash"])
    monkeypatch.setattr(services_module.config, "LLM_DEFAULT_MODEL_ID", "")
    monkeypatch.setattr(services_module.config, "CHUNK_SUMMARY_MODEL_ID", "")
    monkeypatch.setattr(services_module, "Redis", FakeRedis)
    monkeypatch.setattr(services_module, "AsyncOpenAI", FakeLlmClient)

    services = create_services()

    assert services.llm_client.kwargs == {
        "api_key": "missing-api-key",
        "base_url": OPENROUTER_BASE_URL,
    }
    assert all(not option.is_available for option in services.model_options.values())


def test_build_llm_model_options_accepts_prefixed_openrouter_chunk_model(monkeypatch):
    monkeypatch.setattr(services_module.config, "OPENROUTER_API_KEY", "api-key")
    monkeypatch.setattr(services_module.config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(services_module.config, "OPENROUTER_MODEL", "model-a")
    monkeypatch.setattr(services_module.config, "OPENROUTER_MODELS", ["model-a", "google/gemma-4-26b-a4b-it"])
    monkeypatch.setattr(services_module.config, "DEEPSEEK_MODELS", [])
    monkeypatch.setattr(
        services_module.config,
        "CHUNK_SUMMARY_MODEL_ID",
        "openrouter:google/gemma-4-26b-a4b-it",
    )

    options = services_module.build_llm_model_options()
    chunk_model_id = services_module.resolve_chunk_model_id(options)

    assert chunk_model_id == "openrouter:google/gemma-4-26b-a4b-it"
    assert options[chunk_model_id].model_name == "google/gemma-4-26b-a4b-it"


def test_build_llm_model_options_registers_all_openrouter_models(monkeypatch):
    monkeypatch.setattr(services_module.config, "OPENROUTER_API_KEY", "api-key")
    monkeypatch.setattr(services_module.config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        services_module.config,
        "OPENROUTER_MODELS",
        ["model-a", "anthropic/model-b", "google/model-c"],
    )
    monkeypatch.setattr(services_module.config, "DEEPSEEK_MODELS", [])
    monkeypatch.setattr(services_module.config, "CHUNK_SUMMARY_MODEL_ID", "")

    options = services_module.build_llm_model_options()

    assert {
        "openrouter:model-a",
        "openrouter:anthropic/model-b",
        "openrouter:google/model-c",
    }.issubset(options)


@pytest.mark.asyncio
async def test_bot_services_switches_active_model_in_redis():
    redis = FakeRedis()
    openrouter_client = FakeLlmClient()
    deepseek_flash_client = FakeLlmClient()
    deepseek_pro_client = FakeLlmClient()
    services = BotServices(
        redis=redis,
        llm_client=openrouter_client,
        model_name="openrouter-model",
        llm_clients={
            "openrouter:openrouter-model": openrouter_client,
            "deepseek:deepseek-v4-flash": deepseek_flash_client,
            "deepseek:deepseek-v4-pro": deepseek_pro_client,
        },
        model_options={
            "openrouter:openrouter-model": services_module.LlmModelOption(
                model_id="openrouter:openrouter-model",
                provider="OpenRouter",
                label="OpenRouter · openrouter-model",
                model_name="openrouter-model",
                base_url=OPENROUTER_BASE_URL,
                api_key="openrouter-key",
            ),
            "deepseek:deepseek-v4-flash": services_module.LlmModelOption(
                model_id="deepseek:deepseek-v4-flash",
                provider="DeepSeek API",
                label="DeepSeek API · deepseek-v4-flash",
                model_name="deepseek-v4-flash",
                base_url=DEEPSEEK_BASE_URL,
                api_key="deepseek-key",
            ),
            "deepseek:deepseek-v4-pro": services_module.LlmModelOption(
                model_id="deepseek:deepseek-v4-pro",
                provider="DeepSeek API",
                label="DeepSeek API · deepseek-v4-pro",
                model_name="deepseek-v4-pro",
                base_url=DEEPSEEK_BASE_URL,
                api_key="deepseek-key",
            ),
        },
        default_model_id="openrouter:openrouter-model",
    )

    selected = await services.set_active_llm_model(42, "deepseek:deepseek-v4-flash")
    active = await services.get_active_llm_model(42)
    other_chat_active = await services.get_active_llm_model(43)
    fallback_chain = await services.get_llm_fallback_chain(42)
    other_chat_fallback_chain = await services.get_llm_fallback_chain(43)

    assert selected.client is deepseek_flash_client
    assert active.client is deepseek_flash_client
    assert active.option.model_name == "deepseek-v4-flash"
    assert other_chat_active.client is openrouter_client
    assert [model.option.model_id for model in fallback_chain] == [
        "deepseek:deepseek-v4-flash",
        "openrouter:openrouter-model",
        "deepseek:deepseek-v4-pro",
    ]
    assert [model.option.model_id for model in other_chat_fallback_chain] == [
        "openrouter:openrouter-model",
        "deepseek:deepseek-v4-flash",
        "deepseek:deepseek-v4-pro",
    ]
    assert redis.storage == {
        build_model_selection_key(42): "deepseek:deepseek-v4-flash",
    }


@pytest.mark.asyncio
async def test_bot_services_routes_short_logs_to_configured_paid_canary():
    redis = FakeRedis()
    default_client = FakeLlmClient()
    canary_client = FakeLlmClient()
    services = BotServices(
        redis=redis,
        llm_client=default_client,
        model_name="default-model",
        llm_clients={
            "openrouter:default-model": default_client,
            "openrouter:qwen/qwen-2.5-7b-instruct": canary_client,
        },
        model_options={
            "openrouter:default-model": services_module.LlmModelOption(
                model_id="openrouter:default-model",
                provider="OpenRouter",
                label="OpenRouter · default-model",
                model_name="default-model",
                base_url=OPENROUTER_BASE_URL,
                api_key="openrouter-key",
            ),
            "openrouter:qwen/qwen-2.5-7b-instruct": services_module.LlmModelOption(
                model_id="openrouter:qwen/qwen-2.5-7b-instruct",
                provider="OpenRouter",
                label="OpenRouter · qwen/qwen-2.5-7b-instruct",
                model_name="qwen/qwen-2.5-7b-instruct",
                base_url=OPENROUTER_BASE_URL,
                api_key="openrouter-key",
            ),
        },
        default_model_id="openrouter:default-model",
        short_log_model_id="openrouter:qwen/qwen-2.5-7b-instruct",
    )

    short_chain = await services.get_llm_fallback_chain(42, source_message_count=50)
    long_chain = await services.get_llm_fallback_chain(42, source_message_count=51)

    assert [model.option.model_id for model in short_chain] == [
        "openrouter:qwen/qwen-2.5-7b-instruct",
        "openrouter:default-model",
    ]
    assert short_chain[0].client is canary_client
    assert [model.option.model_id for model in long_chain] == ["openrouter:default-model"]


def test_resolve_short_log_model_rejects_free_models(monkeypatch):
    monkeypatch.setattr(services_module.config, "SHORT_LOG_MODEL_ID", "qwen/qwen-2.5-7b-instruct:free")
    options = {
        "openrouter:qwen/qwen-2.5-7b-instruct:free": services_module.LlmModelOption(
            model_id="openrouter:qwen/qwen-2.5-7b-instruct:free",
            provider="OpenRouter",
            label="OpenRouter · qwen/qwen-2.5-7b-instruct:free",
            model_name="qwen/qwen-2.5-7b-instruct:free",
            base_url=OPENROUTER_BASE_URL,
            api_key="openrouter-key",
        ),
    }

    assert services_module.resolve_short_log_model_id(options) == ""


@pytest.mark.asyncio
async def test_bot_services_stores_summary_generation_settings_per_chat():
    redis = FakeRedis()
    services = BotServices(
        redis=redis,
        llm_client=FakeLlmClient(),
        model_name="openrouter-model",
    )

    default_settings = await services.get_summary_generation_settings(43)
    updated_tokens = await services.set_summary_max_output_tokens(42, 2400)
    updated_thinking = await services.set_summary_thinking_mode(42, SUMMARY_THINKING_MODE_ENABLED)
    active_settings = await services.get_summary_generation_settings(42)
    other_chat_settings = await services.get_summary_generation_settings(43)

    assert default_settings.max_output_tokens is None
    assert default_settings.thinking_mode == "disabled"
    assert updated_tokens.max_output_tokens == 2400
    assert updated_thinking.thinking_mode == SUMMARY_THINKING_MODE_ENABLED
    assert active_settings.max_output_tokens == 2400
    assert active_settings.thinking_mode == SUMMARY_THINKING_MODE_ENABLED
    assert other_chat_settings.max_output_tokens is None
    assert other_chat_settings.thinking_mode == "disabled"
    assert redis.storage[build_summary_max_tokens_key(42)] == "2400"
    assert redis.storage[build_summary_thinking_mode_key(42)] == SUMMARY_THINKING_MODE_ENABLED

    reset_settings = await services.set_summary_max_output_tokens(42, None)

    assert reset_settings.max_output_tokens is None
    assert build_summary_max_tokens_key(42) not in redis.storage


@pytest.mark.asyncio
async def test_bot_services_stores_chunking_flag_per_chat():
    redis = FakeRedis()
    services = BotServices(
        redis=redis,
        llm_client=FakeLlmClient(),
        model_name="openrouter-model",
    )

    assert await services.is_chunking_enabled(42) is False

    enabled = await services.set_chunking_enabled(42, True)
    disabled = await services.set_chunking_enabled(43, False)

    assert enabled is True
    assert disabled is False
    assert await services.is_chunking_enabled(42) is True
    assert await services.is_chunking_enabled(43) is False
    assert await services.count_chunking_enabled_chats() == 1
    assert redis.storage[build_chunking_enabled_key(42)] == "1"
    assert build_chunking_enabled_key(43) not in redis.storage


@pytest.mark.asyncio
async def test_bot_services_stores_summary_presentation_per_chat():
    redis = FakeRedis()
    services = BotServices(
        redis=redis,
        llm_client=FakeLlmClient(),
        model_name="openrouter-model",
    )

    default_settings = await services.get_summary_presentation_settings(43)
    updated_settings = await services.set_summary_presentation_settings(
        42,
        style_id="anime_recaper",
        tone_id="friendly",
        aggressiveness=1,
    )
    active_settings = await services.get_summary_presentation_settings(42)
    other_chat_settings = await services.get_summary_presentation_settings(43)

    assert default_settings.style.option_id == "classic_chat_storyteller"
    assert default_settings.tone.option_id == "ironic"
    assert default_settings.aggressiveness.level == 2
    assert active_settings == updated_settings
    assert active_settings.style.option_id == "anime_recaper"
    assert active_settings.tone.option_id == "friendly"
    assert active_settings.aggressiveness.level == 1
    assert other_chat_settings == default_settings
    assert build_summary_presentation_key(42) in redis.storage

    reset_settings = await services.reset_summary_presentation_settings(42)

    assert reset_settings == default_settings
    assert build_summary_presentation_key(42) not in redis.storage


@pytest.mark.asyncio
async def test_bot_services_migrates_legacy_profile_and_rejects_invalid_values():
    redis = FakeRedis()
    redis.storage[build_prompt_profile_key(42)] = b"executive_brief"
    services = BotServices(
        redis=redis,
        llm_client=FakeLlmClient(),
        model_name="openrouter-model",
    )

    settings = await services.get_summary_presentation_settings(42)

    assert settings.style.option_id == "executive_brief"
    assert settings.tone.option_id == "dry"
    assert settings.aggressiveness.level == 0

    with pytest.raises(ValueError):
        await services.set_summary_presentation_settings(42, style_id="missing_profile")


@pytest.mark.asyncio
async def test_bot_services_tracks_chat_approval_status():
    redis = FakeRedis()
    services = BotServices(
        redis=redis,
        llm_client=FakeLlmClient(),
        model_name="openrouter-model",
    )

    default_status = await services.get_chat_approval_status(42)
    seen_status = await services.set_chat_approval_status(42, CHAT_APPROVAL_STATUS_SEEN)
    saved_status = await services.get_saved_chat_approval_status(42)
    reviewed_status = await services.set_chat_approval_status(42, CHAT_APPROVAL_STATUS_REVIEWED)
    left_status = await services.set_chat_approval_status(42, CHAT_APPROVAL_STATUS_LEFT)

    assert default_status == CHAT_APPROVAL_STATUS_SEEN
    assert seen_status == CHAT_APPROVAL_STATUS_SEEN
    assert saved_status == CHAT_APPROVAL_STATUS_SEEN
    assert reviewed_status == CHAT_APPROVAL_STATUS_REVIEWED
    assert left_status == CHAT_APPROVAL_STATUS_LEFT
    assert redis.storage[build_chat_approval_key(42)] == CHAT_APPROVAL_STATUS_LEFT

    with pytest.raises(ValueError):
        await services.set_chat_approval_status(42, "unknown")
