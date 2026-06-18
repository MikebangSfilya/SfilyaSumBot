import html

from aiogram import types

import config
from sumbot.chunks import ChunkRuntimeStats
from sumbot.prompt_builder import SummaryPresentationSettings
from sumbot.services import ActiveLlmModel, BotServices, SummaryGenerationSettings
from sumbot.telegram_handlers.debug_constants import (
    CHUNKING_SETTINGS_CALLBACK_PREFIX,
    DEBUG_DELETE_CALLBACK_DATA,
    LLM_MODEL_SETTINGS_CALLBACK_PREFIX,
    PRESENTATION_OPEN_CALLBACK_DATA,
    SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX,
    SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX,
)
from sumbot.telegram_handlers.settings_panel import (
    build_chunking_settings_row,
    build_summary_thinking_settings_row,
    build_summary_token_settings_row,
    format_chunking_setting,
    format_summary_thinking_mode,
    format_summary_tokens_setting,
)


def build_debug_info(
    redis_logs_count: int,
    last_day_count: int,
    db_status: str,
    active_model: ActiveLlmModel,
    generation_settings: SummaryGenerationSettings | None = None,
    presentation_settings: SummaryPresentationSettings | None = None,
    *,
    chunking_enabled: bool = False,
    chunk_stats: ChunkRuntimeStats | None = None,
) -> str:
    generation_settings = generation_settings or SummaryGenerationSettings()
    chunk_stats = chunk_stats or ChunkRuntimeStats(active_chunk_size=0, summarized_chunk_count=0, last_status="idle")
    return (
        "⚙️ <b>Debug Info</b>\n\n"
        f"🔹 Всего логов в Redis: {redis_logs_count}\n"
        f"🔹 Сообщений за 24ч: {last_day_count}\n"
        f"🔹 Лимит истории: {config.ChatConfig.HISTORY_LIMIT}\n"
        f"🔹 LLM провайдер: {html.escape(active_model.option.provider)}\n"
        f"🔹 Текущая модель: <code>{html.escape(active_model.option.model_name)}</code>\n"
        f"🔹 Output tokens: {html.escape(format_summary_tokens_setting(generation_settings))}\n"
        f"🔹 Thinking: {html.escape(format_summary_thinking_mode(generation_settings))}\n"
        f"🔹 Стиль: {html.escape(presentation_settings.style.label) if presentation_settings else '—'}\n"
        f"🔹 Тон: {html.escape(presentation_settings.tone.label) if presentation_settings else '—'}\n"
        f"🔹 Агрессивность: "
        f"{presentation_settings.aggressiveness.level if presentation_settings else '—'}"
        f"{(' · ' + html.escape(presentation_settings.aggressiveness.label)) if presentation_settings else ''}\n"
        f"🔹 Chunking: {html.escape(format_chunking_setting(chunking_enabled))}\n"
        f"🔹 Active chunk size: {chunk_stats.active_chunk_size}\n"
        f"🔹 Summarized chunks: {chunk_stats.summarized_chunk_count}\n"
        f"🔹 Last chunk status: {html.escape(chunk_stats.last_status)}\n"
        f"🔹 Статус БД: {html.escape(db_status)}\n\n"
        "🗑 <i>Нажми кнопку удаления или сообщение удалится через 3 минуты.</i>"
    )


def build_llm_model_settings_keyboard(
    services: BotServices,
    active_model_id: str,
    generation_settings: SummaryGenerationSettings | None = None,
    presentation_settings: SummaryPresentationSettings | None = None,
    *,
    chunking_enabled: bool = False,
) -> types.InlineKeyboardMarkup:
    generation_settings = generation_settings or SummaryGenerationSettings()
    buttons = build_llm_model_settings_rows(services, active_model_id)
    buttons.append(build_summary_token_settings_row(generation_settings, SUMMARY_TOKENS_SETTINGS_CALLBACK_PREFIX))
    buttons.append(
        build_summary_thinking_settings_row(generation_settings, SUMMARY_THINKING_SETTINGS_CALLBACK_PREFIX)
    )
    buttons.append(build_chunking_settings_row(chunking_enabled, CHUNKING_SETTINGS_CALLBACK_PREFIX))
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="🎛 Настройка пересказа",
                callback_data=PRESENTATION_OPEN_CALLBACK_DATA,
            )
        ]
    )
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="🗑 Удалить сообщение",
                callback_data=DEBUG_DELETE_CALLBACK_DATA,
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def build_llm_model_settings_rows(
    services: BotServices,
    active_model_id: str,
) -> list[list[types.InlineKeyboardButton]]:
    return [
        [
            types.InlineKeyboardButton(
                text=f"{format_model_option_marker(option.model_id == active_model_id, option.is_available)} "
                f"{option.label}",
                callback_data=f"{LLM_MODEL_SETTINGS_CALLBACK_PREFIX}{option.model_id}",
            )
        ]
        for option in services.list_model_options()
    ]


def format_model_option_marker(is_active: bool, is_available: bool) -> str:
    if not is_available:
        return "🔒"
    if is_active:
        return "✅"
    return "▫️"
