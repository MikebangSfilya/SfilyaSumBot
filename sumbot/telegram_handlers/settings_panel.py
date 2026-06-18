from aiogram import types

from sumbot.services import (
    SUMMARY_THINKING_MODE_DISABLED,
    SUMMARY_THINKING_MODE_ENABLED,
    SummaryGenerationSettings,
)

SUMMARY_TOKEN_PRESETS = (1200, 2400, 4000)


def build_summary_token_settings_row(
    generation_settings: SummaryGenerationSettings,
    callback_prefix: str,
) -> list[types.InlineKeyboardButton]:
    buttons = [
        types.InlineKeyboardButton(
            text=f"{'✅' if generation_settings.max_output_tokens is None else '▫️'} Авто",
            callback_data=f"{callback_prefix}auto",
        )
    ]
    buttons.extend(
        types.InlineKeyboardButton(
            text=f"{'✅' if generation_settings.max_output_tokens == token_limit else '▫️'} {token_limit}",
            callback_data=f"{callback_prefix}{token_limit}",
        )
        for token_limit in SUMMARY_TOKEN_PRESETS
    )
    return buttons


def build_summary_thinking_settings_row(
    generation_settings: SummaryGenerationSettings,
    callback_prefix: str,
) -> list[types.InlineKeyboardButton]:
    return [
        types.InlineKeyboardButton(
            text=(
                f"{'✅' if generation_settings.thinking_mode == SUMMARY_THINKING_MODE_DISABLED else '▫️'} "
                "Не думает"
            ),
            callback_data=f"{callback_prefix}{SUMMARY_THINKING_MODE_DISABLED}",
        ),
        types.InlineKeyboardButton(
            text=(
                f"{'✅' if generation_settings.thinking_mode == SUMMARY_THINKING_MODE_ENABLED else '▫️'} "
                "Думает"
            ),
            callback_data=f"{callback_prefix}{SUMMARY_THINKING_MODE_ENABLED}",
        ),
    ]


def build_chunking_settings_row(
    chunking_enabled: bool,
    callback_prefix: str,
) -> list[types.InlineKeyboardButton]:
    return [
        types.InlineKeyboardButton(
            text=f"{'✅' if chunking_enabled else '▫️'} Chunking ON",
            callback_data=f"{callback_prefix}on",
        ),
        types.InlineKeyboardButton(
            text=f"{'✅' if not chunking_enabled else '▫️'} Chunking OFF",
            callback_data=f"{callback_prefix}off",
        ),
    ]


def parse_summary_tokens_callback_value(value: str) -> int | None:
    if value == "auto":
        return None
    max_output_tokens = int(value)
    if max_output_tokens not in SUMMARY_TOKEN_PRESETS:
        raise ValueError(value)
    return max_output_tokens


def format_summary_tokens_setting(generation_settings: SummaryGenerationSettings) -> str:
    if generation_settings.max_output_tokens is None:
        return "auto (180/450/1200)"
    return str(generation_settings.max_output_tokens)


def format_summary_thinking_mode(generation_settings: SummaryGenerationSettings) -> str:
    if generation_settings.thinking_mode == SUMMARY_THINKING_MODE_ENABLED:
        return "думает"
    return "не думает"


def format_chunking_setting(enabled: bool) -> str:
    return "включен" if enabled else "выключен"
