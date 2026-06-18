import time

from aiogram import types

from sumbot.chunks import get_chunk_runtime_stats
from sumbot.constants import DEFAULT_SUMMARY_PERIOD_SECONDS
from sumbot.history import build_chat_history_key
from sumbot.prompt_builder import SummaryPresentationSettings
from sumbot.services import ActiveLlmModel, BotServices, SummaryGenerationSettings
from sumbot.telegram_handlers.debug_panel import (
    build_debug_info,
    build_llm_model_settings_keyboard,
)
from sumbot.telegram_handlers.debug_stats import count_recent_logs


async def refresh_debug_settings_message(
    services: BotServices,
    message: types.Message,
    active_model: ActiveLlmModel,
    generation_settings: SummaryGenerationSettings,
    presentation_settings: SummaryPresentationSettings | None = None,
) -> None:
    raw_logs = await services.redis.lrange(build_chat_history_key(message.chat.id), 0, -1)
    last_day_count = count_recent_logs(
        raw_logs,
        time.time(),
        period_seconds=DEFAULT_SUMMARY_PERIOD_SECONDS,
    )
    db_status = "✅ Подключена" if services.db_engine else "❌ Нет связи"
    chunking_enabled = await services.is_chunking_enabled(message.chat.id)
    chunk_stats = await get_chunk_runtime_stats(services.redis, message.chat.id)
    if presentation_settings is None:
        presentation_settings = await services.get_summary_presentation_settings(message.chat.id)
    await message.edit_text(
        build_debug_info(
            len(raw_logs),
            last_day_count,
            db_status,
            active_model,
            generation_settings,
            presentation_settings,
            chunking_enabled=chunking_enabled,
            chunk_stats=chunk_stats,
        ),
        parse_mode="HTML",
        reply_markup=build_llm_model_settings_keyboard(
            services,
            active_model.option.model_id,
            generation_settings,
            presentation_settings,
            chunking_enabled=chunking_enabled,
        ),
    )
