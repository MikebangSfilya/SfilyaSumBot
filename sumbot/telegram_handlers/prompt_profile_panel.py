import html

from aiogram import types

from sumbot.prompt_builder import AGGRESSIVENESS_OPTIONS, SummaryPresentationSettings
from sumbot.services import BotServices
from sumbot.telegram_handlers.debug_constants import (
    PRESENTATION_DELETE_CALLBACK_DATA,
    PRESENTATION_MAIN_CALLBACK_DATA,
    PRESENTATION_MENU_CALLBACK_PREFIX,
    PRESENTATION_RESET_CALLBACK_DATA,
    PRESENTATION_SET_CALLBACK_PREFIX,
)

PRESENTATION_COMPONENT_STYLE = "s"
PRESENTATION_COMPONENT_TONE = "t"
PRESENTATION_COMPONENT_AGGRESSIVENESS = "a"
PRESENTATION_COMPONENTS = {
    PRESENTATION_COMPONENT_STYLE,
    PRESENTATION_COMPONENT_TONE,
    PRESENTATION_COMPONENT_AGGRESSIVENESS,
}


def build_presentation_panel_info(chat: types.Chat, settings: SummaryPresentationSettings) -> str:
    chat_title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id)
    return (
        "🎛 <b>Настройка пересказа</b>\n\n"
        f"Чат: {html.escape(str(chat_title))}\n"
        f"Стиль: <b>{html.escape(settings.style.label)}</b>\n"
        f"Тон: <b>{html.escape(settings.tone.label)}</b>\n"
        f"Агрессивность: <b>{settings.aggressiveness.level} · "
        f"{html.escape(settings.aggressiveness.label)}</b>\n\n"
        "Настройки применятся к следующим пересказам этого чата."
    )


def build_presentation_panel_keyboard(settings: SummaryPresentationSettings) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"🎭 Стиль: {settings.style.label}",
                    callback_data=f"{PRESENTATION_MENU_CALLBACK_PREFIX}{PRESENTATION_COMPONENT_STYLE}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=f"🗣 Тон: {settings.tone.label}",
                    callback_data=f"{PRESENTATION_MENU_CALLBACK_PREFIX}{PRESENTATION_COMPONENT_TONE}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=(
                        f"🔥 Агрессивность: {settings.aggressiveness.level} · "
                        f"{settings.aggressiveness.label}"
                    ),
                    callback_data=(
                        f"{PRESENTATION_MENU_CALLBACK_PREFIX}{PRESENTATION_COMPONENT_AGGRESSIVENESS}"
                    ),
                )
            ],
            [types.InlineKeyboardButton(text="↩️ Сбросить", callback_data=PRESENTATION_RESET_CALLBACK_DATA)],
            [
                types.InlineKeyboardButton(
                    text="🗑 Удалить сообщение",
                    callback_data=PRESENTATION_DELETE_CALLBACK_DATA,
                )
            ],
        ]
    )


def build_presentation_option_info(
    chat: types.Chat,
    settings: SummaryPresentationSettings,
    component: str,
) -> str:
    labels = {
        PRESENTATION_COMPONENT_STYLE: "стиль",
        PRESENTATION_COMPONENT_TONE: "тон",
        PRESENTATION_COMPONENT_AGGRESSIVENESS: "агрессивность",
    }
    return f"🎛 <b>Выбери {labels[component]} пересказа</b>\n\n" + build_presentation_panel_info(
        chat,
        settings,
    ).split("\n\n", maxsplit=1)[1]


def build_presentation_option_keyboard(
    services: BotServices,
    settings: SummaryPresentationSettings,
    component: str,
) -> types.InlineKeyboardMarkup:
    if component == PRESENTATION_COMPONENT_STYLE:
        options = tuple((option.option_id, option.label) for option in services.list_summary_styles())
        active_value = settings.style.option_id
    elif component == PRESENTATION_COMPONENT_TONE:
        options = tuple((option.option_id, option.label) for option in services.list_summary_tones())
        active_value = settings.tone.option_id
    elif component == PRESENTATION_COMPONENT_AGGRESSIVENESS:
        options = tuple((str(option.level), f"{option.level} · {option.label}") for option in AGGRESSIVENESS_OPTIONS)
        active_value = str(settings.aggressiveness.level)
    else:
        raise ValueError(component)

    rows = [
        [
            types.InlineKeyboardButton(
                text=f"{'✅' if value == active_value else '▫️'} {label}",
                callback_data=f"{PRESENTATION_SET_CALLBACK_PREFIX}{component}:{value}",
            )
        ]
        for value, label in options
    ]
    rows.append([types.InlineKeyboardButton(text="← Назад", callback_data=PRESENTATION_MAIN_CALLBACK_DATA)])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def parse_presentation_set_callback(data: str) -> tuple[str, str] | None:
    if not data.startswith(PRESENTATION_SET_CALLBACK_PREFIX):
        return None
    payload = data.removeprefix(PRESENTATION_SET_CALLBACK_PREFIX)
    component, separator, value = payload.partition(":")
    if not separator or component not in PRESENTATION_COMPONENTS or not value:
        return None
    return component, value


def format_presentation_settings(settings: SummaryPresentationSettings) -> str:
    return (
        f"{settings.style.label} / {settings.tone.label} / "
        f"{settings.aggressiveness.level} · {settings.aggressiveness.label}"
    )
