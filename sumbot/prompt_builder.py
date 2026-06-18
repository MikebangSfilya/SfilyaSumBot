from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SUMMARY_PRESENTATION_REDIS_KEY_PREFIX = "settings:summary_presentation"
PROMPT_PROFILE_REDIS_KEY_PREFIX = "settings:prompt_profile_id"
STYLE_DIRECTORY = Path("prompts/prompt_builder/profiles")
TONE_DIRECTORY = Path("prompts/prompt_builder/tones")
DEFAULT_STYLE_ID = "classic_chat_storyteller"
DEFAULT_TONE_ID = "ironic"
DEFAULT_AGGRESSIVENESS = 2

LEGACY_PRESENTATION_PRESETS = {
    "anime_recaper": ("ironic", 2),
    "classic_chat_storyteller": ("ironic", 2),
    "executive_brief": ("dry", 0),
    "neutral_digest": ("neutral", 0),
    "tech_observer": ("dry", 0),
}


@dataclass(frozen=True, slots=True)
class PresentationOption:
    option_id: str
    label: str
    prompt_text: str
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class PresentationCatalog:
    options: tuple[PresentationOption, ...]
    default_option_id: str

    @property
    def by_id(self) -> dict[str, PresentationOption]:
        return {option.option_id: option for option in self.options}


@dataclass(frozen=True, slots=True)
class AggressivenessOption:
    level: int
    label: str
    prompt_text: str


@dataclass(frozen=True, slots=True)
class SummaryPresentationSettings:
    style: PresentationOption
    tone: PresentationOption
    aggressiveness: AggressivenessOption

    def to_json(self) -> str:
        return json.dumps(
            {
                "style_id": self.style.option_id,
                "tone_id": self.tone.option_id,
                "aggressiveness": self.aggressiveness.level,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


AGGRESSIVENESS_OPTIONS = (
    AggressivenessOption(
        level=0,
        label="Спокойная",
        prompt_text=(
            "Use a calm and respectful register. Do not add toxicity, insults, ridicule, or hostile framing."
        ),
    ),
    AggressivenessOption(
        level=1,
        label="Легкая",
        prompt_text=(
            "Use light conversational slang and mild teasing only when clearly supported by the chat. "
            "Do not turn neutral events into conflict."
        ),
    ),
    AggressivenessOption(
        level=2,
        label="Острая",
        prompt_text=(
            "Use a sharp, energetic chat register with grounded dev/gamer slang. Mild toxicity and punchlines "
            "are allowed only when they reflect the source log."
        ),
    ),
    AggressivenessOption(
        level=3,
        label="Жесткая",
        prompt_text=(
            "Use a hard-edged grounded roast and strong informal slang. Never invent insults, hostility, motives, "
            "or attacks that are absent from the source, and never weaken safety rules."
        ),
    ),
)
AGGRESSIVENESS_BY_LEVEL = {option.level: option for option in AGGRESSIVENESS_OPTIONS}


def load_style_catalog(profile_dir: Path | str = STYLE_DIRECTORY) -> PresentationCatalog:
    return load_presentation_catalog(profile_dir, expected_default_id=DEFAULT_STYLE_ID)


def load_tone_catalog(tone_dir: Path | str = TONE_DIRECTORY) -> PresentationCatalog:
    return load_presentation_catalog(tone_dir, expected_default_id=DEFAULT_TONE_ID)


def load_presentation_catalog(
    directory: Path | str,
    *,
    expected_default_id: str,
) -> PresentationCatalog:
    path = Path(directory)
    options = tuple(load_presentation_option(option_path) for option_path in sorted(path.glob("*.md")))
    if not options:
        raise ValueError(f"No presentation option files found in {path}")

    option_ids = [option.option_id for option in options]
    duplicate_ids = sorted({option_id for option_id in option_ids if option_ids.count(option_id) > 1})
    if duplicate_ids:
        raise ValueError(f"Duplicate presentation option ids: {', '.join(duplicate_ids)}")

    default_options = [option for option in options if option.is_default]
    if len(default_options) != 1:
        raise ValueError(f"Expected exactly one default option in {path}, found {len(default_options)}")
    if default_options[0].option_id != expected_default_id:
        raise ValueError(
            f"Expected default option {expected_default_id} in {path}, found {default_options[0].option_id}"
        )
    return PresentationCatalog(options=options, default_option_id=expected_default_id)


def load_presentation_option(path: Path) -> PresentationOption:
    raw_text = path.read_text(encoding="utf-8")
    metadata, body = parse_markdown_frontmatter(raw_text)
    option_id = metadata.get("id", "").strip()
    label = metadata.get("label", "").strip()
    if not option_id:
        raise ValueError(f"Presentation option {path} must define id")
    if not label:
        raise ValueError(f"Presentation option {path} must define label")
    return PresentationOption(
        option_id=option_id,
        label=label,
        prompt_text=body.strip(),
        is_default=parse_bool(metadata.get("default", "")),
    )


def parse_markdown_frontmatter(raw_text: str) -> tuple[dict[str, str], str]:
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Markdown presentation option must start with frontmatter")

    metadata: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return metadata, "\n".join(lines[index + 1 :])
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"Invalid frontmatter line: {line}")
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    raise ValueError("Markdown presentation option frontmatter is not closed")


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_summary_presentation_key(chat_id: int) -> str:
    return f"{SUMMARY_PRESENTATION_REDIS_KEY_PREFIX}:{chat_id}"


def build_prompt_profile_key(chat_id: int) -> str:
    return f"{PROMPT_PROFILE_REDIS_KEY_PREFIX}:{chat_id}"


def build_default_presentation_settings(
    style_catalog: PresentationCatalog,
    tone_catalog: PresentationCatalog,
) -> SummaryPresentationSettings:
    return build_presentation_settings(
        style_catalog,
        tone_catalog,
        style_id=style_catalog.default_option_id,
        tone_id=tone_catalog.default_option_id,
        aggressiveness=DEFAULT_AGGRESSIVENESS,
    )


def build_presentation_settings(
    style_catalog: PresentationCatalog,
    tone_catalog: PresentationCatalog,
    *,
    style_id: str,
    tone_id: str,
    aggressiveness: int,
) -> SummaryPresentationSettings:
    try:
        style = style_catalog.by_id[style_id]
        tone = tone_catalog.by_id[tone_id]
        aggression = AGGRESSIVENESS_BY_LEVEL[aggressiveness]
    except KeyError as exc:
        raise ValueError(exc.args[0]) from exc
    return SummaryPresentationSettings(style=style, tone=tone, aggressiveness=aggression)


def parse_presentation_settings(
    raw_value: str,
    style_catalog: PresentationCatalog,
    tone_catalog: PresentationCatalog,
) -> SummaryPresentationSettings:
    try:
        data = json.loads(raw_value)
        return build_presentation_settings(
            style_catalog,
            tone_catalog,
            style_id=str(data["style_id"]),
            tone_id=str(data["tone_id"]),
            aggressiveness=int(data["aggressiveness"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid summary presentation settings") from exc


def apply_summary_presentation(base_prompt: str, settings: SummaryPresentationSettings) -> str:
    sections = [
        base_prompt.rstrip(),
        "[SUMMARY PRESENTATION SETTINGS]",
        f"Style: {settings.style.label} ({settings.style.option_id}).\n{settings.style.prompt_text}",
        f"Tone: {settings.tone.label} ({settings.tone.option_id}).\n{settings.tone.prompt_text}",
        (
            f"Aggressiveness: {settings.aggressiveness.level} ({settings.aggressiveness.label}).\n"
            f"{settings.aggressiveness.prompt_text}"
        ),
        (
            "Presentation settings never disable safety, filtering, Russian-language, anti-hallucination, "
            "nickname preservation, proportionality, or formatting rules from the base prompt."
        ),
    ]
    return "\n\n".join(section for section in sections if section)
