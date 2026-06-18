import html

from aiogram import types

from sumbot.chat_approval import CHAT_APPROVAL_STATUS_LEFT, CHAT_APPROVAL_STATUS_SEEN
from sumbot.chat_registry import KnownBotChat
from sumbot.chunks import ChunkRuntimeStats
from sumbot.prompt_builder import AGGRESSIVENESS_OPTIONS, SummaryPresentationSettings
from sumbot.services import ActiveLlmModel, BotServices, SummaryGenerationSettings
from sumbot.telegram_handlers.prompt_profile_panel import (
    PRESENTATION_COMPONENT_AGGRESSIVENESS,
    PRESENTATION_COMPONENT_STYLE,
    PRESENTATION_COMPONENT_TONE,
    PRESENTATION_COMPONENTS,
)
from sumbot.telegram_handlers.settings_panel import (
    build_chunking_settings_row,
    build_summary_thinking_settings_row,
    build_summary_token_settings_row,
    format_chunking_setting,
    format_summary_thinking_mode,
    format_summary_tokens_setting,
)

ADMIN_CHAT_SETTINGS_COMMAND = "debug_chat_settings"
ADMIN_CHAT_SETTINGS_PAGE_SIZE = 8
ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX = "admin_chat_page:"
ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX = "admin_chat_select:"
ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX = "admin_chat_model:"
ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX = "admin_chat_tokens:"
ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX = "admin_chat_thinking:"
ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX = "admin_chat_chunking:"
ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX = "acpm:"
ADMIN_CHAT_PRESENTATION_SET_CALLBACK_PREFIX = "acps:"
ADMIN_CHAT_PRESENTATION_BACK_CALLBACK_PREFIX = "acpb:"
ADMIN_CHAT_PRESENTATION_RESET_CALLBACK_PREFIX = "acpr:"
ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX = "acl:"
ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX = "aclc:"
ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX = "acr:"
ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA = "acb:0"


def parse_admin_chat_settings_target(command_text: str) -> int | None:
    parts = command_text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    target_value = parts[1].strip()
    if not target_value:
        return None
    return int(target_value)


def build_admin_chat_settings_list_text(
    chats: list[KnownBotChat],
    page: int,
    *,
    chunking_enabled_chats: int = 0,
) -> str:
    if not chats:
        return (
            "Нет сохраненных чатов.\n"
            "Они появятся после новых сообщений или событий с ботом."
        )

    total_pages = max(1, (len(chats) + ADMIN_CHAT_SETTINGS_PAGE_SIZE - 1) // ADMIN_CHAT_SETTINGS_PAGE_SIZE)
    normalized_page = min(max(0, page), total_pages - 1)
    start = normalized_page * ADMIN_CHAT_SETTINGS_PAGE_SIZE
    shown_chats = chats[start:start + ADMIN_CHAT_SETTINGS_PAGE_SIZE]
    lines = [
        "⚙️ <b>Управление чатами</b>",
        f"Сохраненных чатов: {len(chats)}",
        f"Чатов с chunking: {chunking_enabled_chats}",
        f"Страница: {normalized_page + 1}/{total_pages}",
        "",
        "Выбери чат кнопкой ниже или открой напрямую:",
        f"/{ADMIN_CHAT_SETTINGS_COMMAND} &lt;chat_id&gt;",
    ]
    for index, chat in enumerate(shown_chats, start=start + 1):
        lines.append(
            f"{index}. {format_known_chat_label(chat)} "
            f"(<code>{chat.chat_id}</code>, {html.escape(chat.chat_type)}, {html.escape(chat.bot_status)})"
        )
    return "\n".join(lines)


def build_admin_chat_settings_list_keyboard(
    chats: list[KnownBotChat],
    page: int,
) -> types.InlineKeyboardMarkup | None:
    if not chats:
        return None

    total_pages = max(1, (len(chats) + ADMIN_CHAT_SETTINGS_PAGE_SIZE - 1) // ADMIN_CHAT_SETTINGS_PAGE_SIZE)
    normalized_page = min(max(0, page), total_pages - 1)
    start = normalized_page * ADMIN_CHAT_SETTINGS_PAGE_SIZE
    shown_chats = chats[start:start + ADMIN_CHAT_SETTINGS_PAGE_SIZE]
    buttons = [[_build_admin_chat_settings_select_button(chat)] for chat in shown_chats]

    nav_buttons = _build_admin_chat_settings_navigation(normalized_page, total_pages)
    if nav_buttons:
        buttons.append(nav_buttons)

    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_admin_chat_settings_select_button(chat: KnownBotChat) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=truncate_button_prompttext(format_known_chat_button_label(chat)),
        callback_data=f"{ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX}{chat.chat_id}",
    )


def _build_admin_chat_settings_navigation(
    current_page: int,
    total_pages: int,
) -> list[types.InlineKeyboardButton]:
    buttons = []
    if current_page > 0:
        buttons.append(
            types.InlineKeyboardButton(
                text="← Назад",
                callback_data=f"{ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX}{current_page - 1}",
            )
        )
    if current_page + 1 < total_pages:
        buttons.append(
            types.InlineKeyboardButton(
                text="Вперед →",
                callback_data=f"{ADMIN_CHAT_SETTINGS_PAGE_CALLBACK_PREFIX}{current_page + 1}",
            )
        )
    return buttons


def build_admin_chat_settings_info(
    target_chat_id: int,
    active_model: ActiveLlmModel,
    generation_settings: SummaryGenerationSettings,
    presentation_settings: SummaryPresentationSettings | None = None,
    *,
    known_chat: KnownBotChat | None = None,
    chat_approval_status: str = CHAT_APPROVAL_STATUS_SEEN,
    chunking_enabled: bool = False,
    chunking_enabled_chats: int = 0,
    chunk_stats: ChunkRuntimeStats | None = None,
) -> str:
    chunk_stats = chunk_stats or ChunkRuntimeStats(active_chunk_size=0, summarized_chunk_count=0, last_status="idle")
    if known_chat is None:
        chat_title = f"chat {target_chat_id}"
        chat_meta = "нет в локальном реестре"
    else:
        chat_title = format_known_chat_label(known_chat)
        chat_meta = (
            f"type: {html.escape(known_chat.chat_type)}, "
            f"visibility: {'public' if known_chat.is_public else 'private'}, "
            f"bot_status: {html.escape(known_chat.bot_status)}"
        )
        if known_chat.public_link:
            chat_meta += f"\nlink: {html.escape(known_chat.public_link)}"

    return (
        "⚙️ <b>Настройки чата</b>\n\n"
        f"<b>{chat_title}</b>\n"
        f"id: <code>{target_chat_id}</code>\n"
        f"{chat_meta}\n"
        f"approval_status: {html.escape(chat_approval_status)}\n\n"
        f"LLM provider: {html.escape(active_model.option.provider)}\n"
        f"Модель: <code>{html.escape(active_model.option.model_name)}</code>\n"
        f"Output tokens: {html.escape(format_summary_tokens_setting(generation_settings))}\n"
        f"Thinking: {html.escape(format_summary_thinking_mode(generation_settings))}\n"
        f"Стиль: {html.escape(presentation_settings.style.label) if presentation_settings else '—'}\n"
        f"Тон: {html.escape(presentation_settings.tone.label) if presentation_settings else '—'}\n"
        f"Агрессивность: "
        f"{presentation_settings.aggressiveness.level if presentation_settings else '—'}"
        f"{(' · ' + html.escape(presentation_settings.aggressiveness.label)) if presentation_settings else ''}\n"
        f"Chunking: {html.escape(format_chunking_setting(chunking_enabled))}\n"
        f"Чатов с chunking: {chunking_enabled_chats}\n"
        f"Active chunk size: {chunk_stats.active_chunk_size}\n"
        f"Summarized chunks: {chunk_stats.summarized_chunk_count}\n"
        f"Last chunk status: {html.escape(chunk_stats.last_status)}"
    )


def build_admin_chat_settings_keyboard(
    services: BotServices,
    target_chat_id: int,
    active_model_id: str,
    generation_settings: SummaryGenerationSettings,
    presentation_settings: SummaryPresentationSettings | None = None,
    *,
    known_chat: KnownBotChat | None = None,
    chat_approval_status: str = CHAT_APPROVAL_STATUS_SEEN,
    chunking_enabled: bool = False,
) -> types.InlineKeyboardMarkup:
    buttons = build_admin_chat_model_settings_rows(services, target_chat_id, active_model_id)
    buttons.append(
        build_summary_token_settings_row(
            generation_settings,
            f"{ADMIN_CHAT_SETTINGS_TOKENS_CALLBACK_PREFIX}{target_chat_id}:",
        )
    )
    buttons.append(
        build_summary_thinking_settings_row(
            generation_settings,
            f"{ADMIN_CHAT_SETTINGS_THINKING_CALLBACK_PREFIX}{target_chat_id}:",
        )
    )
    buttons.append(
        build_chunking_settings_row(
            chunking_enabled,
            f"{ADMIN_CHAT_SETTINGS_CHUNKING_CALLBACK_PREFIX}{target_chat_id}:",
        )
    )
    if presentation_settings is not None:
        buttons.extend(
            [
                [
                    types.InlineKeyboardButton(
                        text=f"🎭 Стиль: {presentation_settings.style.label}",
                        callback_data=(
                            f"{ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX}{target_chat_id}:"
                            f"{PRESENTATION_COMPONENT_STYLE}"
                        ),
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=f"🗣 Тон: {presentation_settings.tone.label}",
                        callback_data=(
                            f"{ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX}{target_chat_id}:"
                            f"{PRESENTATION_COMPONENT_TONE}"
                        ),
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=(
                            f"🔥 Агрессивность: {presentation_settings.aggressiveness.level} · "
                            f"{presentation_settings.aggressiveness.label}"
                        ),
                        callback_data=(
                            f"{ADMIN_CHAT_PRESENTATION_MENU_CALLBACK_PREFIX}{target_chat_id}:"
                            f"{PRESENTATION_COMPONENT_AGGRESSIVENESS}"
                        ),
                    )
                ],
            ]
        )
    if chat_approval_status == CHAT_APPROVAL_STATUS_LEFT:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text="Разрешить повторное добавление",
                    callback_data=f"{ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX}{target_chat_id}",
                )
            ]
        )
    elif known_chat is None or known_chat.bot_status not in {"left", "kicked"}:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text="Удалить бота из чата",
                    callback_data=f"{ADMIN_CHAT_SETTINGS_LEAVE_CALLBACK_PREFIX}{target_chat_id}",
                )
            ]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="← Назад к списку чатов",
                callback_data=ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA,
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def build_admin_presentation_option_text(
    target_chat_id: int,
    settings: SummaryPresentationSettings,
    component: str,
) -> str:
    labels = {
        PRESENTATION_COMPONENT_STYLE: "стиль",
        PRESENTATION_COMPONENT_TONE: "тон",
        PRESENTATION_COMPONENT_AGGRESSIVENESS: "агрессивность",
    }
    return (
        f"🎛 <b>Выбери {labels[component]} пересказа</b>\n\n"
        f"chat_id: <code>{target_chat_id}</code>\n"
        f"Стиль: <b>{html.escape(settings.style.label)}</b>\n"
        f"Тон: <b>{html.escape(settings.tone.label)}</b>\n"
        f"Агрессивность: <b>{settings.aggressiveness.level} · "
        f"{html.escape(settings.aggressiveness.label)}</b>"
    )


def build_admin_presentation_option_keyboard(
    services: BotServices,
    target_chat_id: int,
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
                callback_data=(
                    f"{ADMIN_CHAT_PRESENTATION_SET_CALLBACK_PREFIX}{target_chat_id}:"
                    f"{component}:{value}"
                ),
            )
        ]
        for value, label in options
    ]
    rows.extend(
        [
            [
                types.InlineKeyboardButton(
                    text="↩️ Сбросить оформление",
                    callback_data=f"{ADMIN_CHAT_PRESENTATION_RESET_CALLBACK_PREFIX}{target_chat_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="← Назад к настройкам чата",
                    callback_data=f"{ADMIN_CHAT_PRESENTATION_BACK_CALLBACK_PREFIX}{target_chat_id}",
                )
            ],
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def parse_admin_presentation_set_callback(data: str) -> tuple[int, str, str] | None:
    if not data.startswith(ADMIN_CHAT_PRESENTATION_SET_CALLBACK_PREFIX):
        return None
    payload = data.removeprefix(ADMIN_CHAT_PRESENTATION_SET_CALLBACK_PREFIX)
    chat_id_value, separator, remainder = payload.partition(":")
    component, value_separator, value = remainder.partition(":")
    if not separator or not value_separator or component not in PRESENTATION_COMPONENTS or not value:
        return None
    try:
        return int(chat_id_value), component, value
    except ValueError:
        return None


def build_admin_chat_leave_confirmation_text(target_chat_id: int, known_chat: KnownBotChat | None) -> str:
    chat_title = format_known_chat_label(known_chat) if known_chat is not None else f"chat {target_chat_id}"
    return (
        "⚠️ <b>Подтвердить удаление бота?</b>\n\n"
        f"<b>{chat_title}</b>\n"
        f"id: <code>{target_chat_id}</code>\n\n"
        "Бот немедленно выйдет из чата. Повторное добавление будет заблокировано, "
        "пока ты явно не снимешь флаг в этой панели."
    )


def build_admin_chat_leave_confirmation_keyboard(target_chat_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Да, удалить бота",
                    callback_data=f"{ADMIN_CHAT_SETTINGS_LEAVE_CONFIRM_CALLBACK_PREFIX}{target_chat_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=f"{ADMIN_CHAT_SETTINGS_SELECT_CALLBACK_PREFIX}{target_chat_id}",
                )
            ],
        ]
    )


def build_admin_chat_removed_text(target_chat_id: int) -> str:
    return (
        f"Бот удален из чата <code>{target_chat_id}</code>.\n"
        "Повторное добавление заблокировано."
    )


def build_admin_chat_removed_keyboard(target_chat_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Разрешить повторное добавление",
                    callback_data=f"{ADMIN_CHAT_SETTINGS_RESTORE_CALLBACK_PREFIX}{target_chat_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="← Назад к списку чатов",
                    callback_data=ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA,
                )
            ],
        ]
    )


def build_admin_chat_readd_allowed_text(target_chat_id: int) -> str:
    return (
        f"Повторное добавление бота в чат <code>{target_chat_id}</code> разрешено.\n\n"
        "Бот не может вернуться в Telegram сам: добавить его обратно может любой участник, "
        "если настройки чата это разрешают."
    )


def build_admin_chat_back_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="← Назад к списку чатов",
                    callback_data=ADMIN_CHAT_SETTINGS_BACK_CALLBACK_DATA,
                )
            ]
        ]
    )


def build_admin_chat_model_settings_rows(
    services: BotServices,
    target_chat_id: int,
    active_model_id: str,
) -> list[list[types.InlineKeyboardButton]]:
    return [
        [
            types.InlineKeyboardButton(
                text=f"{format_admin_chat_model_marker(option.model_id == active_model_id, option.is_available)} "
                f"{option.label}",
                callback_data=f"{ADMIN_CHAT_SETTINGS_MODEL_CALLBACK_PREFIX}{target_chat_id}:{index}",
            )
        ]
        for index, option in enumerate(services.list_model_options())
    ]


def format_admin_chat_model_marker(is_active: bool, is_available: bool) -> str:
    if not is_available:
        return "🔒"
    if is_active:
        return "✅"
    return "▫️"


def parse_admin_chat_setting_callback(data: str, prefix: str) -> tuple[int, str] | None:
    if not data.startswith(prefix):
        return None
    payload = data.removeprefix(prefix)
    chat_id_value, separator, value = payload.partition(":")
    if not separator or not value:
        return None
    try:
        return int(chat_id_value), value
    except ValueError:
        return None


def format_known_chat_label(chat: KnownBotChat) -> str:
    title = html.escape(chat.title)
    if chat.username:
        return f"{title} (@{html.escape(chat.username)})"
    return title


def format_known_chat_button_label(chat: KnownBotChat) -> str:
    visibility = "public" if chat.is_public else "private"
    return f"{chat.title} · {chat.bot_status} · {visibility} · {chat.chat_id}"


def truncate_button_prompttext(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"
