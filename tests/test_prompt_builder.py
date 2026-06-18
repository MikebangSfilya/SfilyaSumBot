import pytest

from sumbot.prompt_builder import (
    build_default_presentation_settings,
    build_presentation_settings,
    build_prompt_profile_key,
    build_summary_presentation_key,
    load_style_catalog,
    load_tone_catalog,
    apply_summary_presentation,
    parse_markdown_frontmatter,
    parse_presentation_settings,
)


def test_load_presentation_catalogs_from_markdown():
    styles = load_style_catalog()
    tones = load_tone_catalog()

    assert styles.default_option_id == "classic_chat_storyteller"
    assert [option.option_id for option in styles.options] == [
        "anime_recaper",
        "classic_chat_storyteller",
        "executive_brief",
        "neutral_digest",
        "tech_observer",
    ]
    assert tones.default_option_id == "ironic"
    assert [option.option_id for option in tones.options] == ["dry", "friendly", "ironic", "neutral"]


def test_default_presentation_preserves_current_behavior():
    settings = build_default_presentation_settings(load_style_catalog(), load_tone_catalog())

    assert settings.style.option_id == "classic_chat_storyteller"
    assert settings.tone.option_id == "ironic"
    assert settings.aggressiveness.level == 2


def test_apply_summary_presentation_appends_all_independent_dimensions():
    settings = build_presentation_settings(
        load_style_catalog(),
        load_tone_catalog(),
        style_id="executive_brief",
        tone_id="dry",
        aggressiveness=0,
    )

    prompt = apply_summary_presentation("base prompt", settings)

    assert prompt.startswith("base prompt")
    assert "[SUMMARY PRESENTATION SETTINGS]" in prompt
    assert "executive_brief" in prompt
    assert "dry" in prompt
    assert "Aggressiveness: 0" in prompt
    assert "never disable safety" in prompt


def test_presentation_settings_round_trip_as_atomic_json():
    styles = load_style_catalog()
    tones = load_tone_catalog()
    settings = build_presentation_settings(
        styles,
        tones,
        style_id="anime_recaper",
        tone_id="friendly",
        aggressiveness=1,
    )

    restored = parse_presentation_settings(settings.to_json(), styles, tones)

    assert restored == settings


def test_parse_markdown_frontmatter_requires_closed_metadata():
    with pytest.raises(ValueError):
        parse_markdown_frontmatter("---\nid: broken")


def test_presentation_keys_are_chat_scoped_and_keep_legacy_key():
    assert build_summary_presentation_key(42) == "settings:summary_presentation:42"
    assert build_prompt_profile_key(42) == "settings:prompt_profile_id:42"
