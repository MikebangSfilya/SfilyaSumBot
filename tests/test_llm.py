from types import SimpleNamespace

import pytest

from anonymizer import Anonymizer
import sumbot.llm as llm
from sumbot.constants import (
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_MEDIUM_LOG_MAX_OUTPUT_TOKENS,
    SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS,
)
from sumbot.database import SummaryExample
from sumbot.llm import (
    build_chunk_chat_completion_kwargs,
    build_chat_completion_kwargs,
    build_system_prompt,
    classify_llm_error,
    generate_chunk_summary,
    generate_summary,
    get_llm_attempt_timeout_seconds,
    get_summary_max_tokens,
    prepare_final_message,
    should_retry_after_error,
    strip_json_code_fence,
    strip_role_tags_from_summary,
    summarize_exception_for_log,
    strip_thinking_tags,
)
from sumbot.summary_context import PreparedSummaryContext


class FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="<think>private</think>User_1 said hi")
                )
            ],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )


class FakeClient:
    def __init__(self):
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class FakeEmptyThenSuccessCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = "" if len(self.calls) == 1 else "User_1 recovered"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )


class FakeEmptyThenSuccessClient:
    def __init__(self):
        self.completions = FakeEmptyThenSuccessCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class FakeInvalidThenSuccessCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = "# Итог\n**User_1 починил Redis**" if len(self.calls) == 1 else "User_1 починил Redis."
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )


class FakeInvalidThenSuccessClient:
    def __init__(self):
        self.completions = FakeInvalidThenSuccessCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class FakeAlwaysInvalidCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="# Итог"))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )


class FakeAlwaysInvalidClient:
    def __init__(self):
        self.completions = FakeAlwaysInvalidCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class FakeTruncatedCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content="User_1 started but"),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=1200),
        )


class FakeTruncatedClient:
    def __init__(self):
        self.completions = FakeTruncatedCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class FakeChunkCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"topics":["topic"],"events":[{"speaker_ref":"speaker_id_1","text":"event"}],"open_loops":[]}'),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=12),
        )


class FakeChunkClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeChunkCompletions())


def test_strip_json_code_fence_removes_outer_json_fence():
    assert strip_json_code_fence('```json\n{"events":[]}\n```') == '{"events":[]}'


def test_strip_json_code_fence_keeps_plain_json():
    assert strip_json_code_fence(' {"events":[]} ') == '{"events":[]}'


def test_strip_thinking_tags_removes_private_reasoning():
    assert strip_thinking_tags("<think>hidden</think>public") == "public"


def test_build_system_prompt_adds_short_log_override_and_request_salt(monkeypatch):
    monkeypatch.setattr(llm.time, "time", lambda: 123.0)

    prompt = build_system_prompt("base", 1, chat_id=42)

    assert prompt.startswith("base")
    assert "[DYNAMIC OVERRIDE]" in prompt
    assert "1-2 коротких предложения" in prompt
    assert "4-5 абзацев" not in prompt
    assert "[Request-Salt: Chat:42_Time:123.0]" in prompt


def test_build_system_prompt_adds_medium_log_override(monkeypatch):
    monkeypatch.setattr(llm.time, "time", lambda: 123.0)

    prompt = build_system_prompt("base", 12, chat_id=42)

    assert "Лог небольшой" in prompt
    assert "2-4 предложениях" in prompt


def test_build_system_prompt_replaces_static_example(monkeypatch):
    monkeypatch.setattr(llm.time, "time", lambda: 123.0)
    example = SummaryExample(
        summary_log_id=33,
        input_log="User_1: сервер лег",
        ideal_summary="User_1 тильтанул из-за сервера.",
    )

    prompt = build_system_prompt(
        "base\n<example>\nold static example\n</example>\nend",
        30,
        chat_id=42,
        dynamic_example=example,
    )

    assert "old static example" not in prompt
    assert "<input_log>\nUser_1: сервер лег\n</input_log>" in prompt
    assert "<ideal_summary>\nUser_1 тильтанул из-за сервера.\n</ideal_summary>" in prompt
    assert "[Request-Salt: Chat:42_Time:123.0]" in prompt


def test_build_system_prompt_keeps_static_example_without_dynamic_example(monkeypatch):
    monkeypatch.setattr(llm.time, "time", lambda: 123.0)

    prompt = build_system_prompt(
        "base\n<example>static</example>",
        30,
        chat_id=42,
    )

    assert "<example>static</example>" in prompt


def test_get_summary_max_tokens_scales_with_message_count():
    assert get_summary_max_tokens(0) == SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS
    assert get_summary_max_tokens(9) == SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS
    assert get_summary_max_tokens(10) == SUMMARY_MEDIUM_LOG_MAX_OUTPUT_TOKENS
    assert get_summary_max_tokens(29) == SUMMARY_MEDIUM_LOG_MAX_OUTPUT_TOKENS
    assert get_summary_max_tokens(30) == SUMMARY_MAX_OUTPUT_TOKENS


def test_get_llm_attempt_timeout_uses_remaining_deadline(monkeypatch):
    monkeypatch.setattr(llm.time, "monotonic", lambda: 100.0)

    assert get_llm_attempt_timeout_seconds(deadline=120.0) == 20.0


def test_summarize_exception_for_log_keeps_logs_single_line_and_short():
    summary = summarize_exception_for_log(RuntimeError("line one\n" + "x" * 300))

    assert "\n" not in summary
    assert summary.startswith("line one ")
    assert summary.endswith("...")
    assert len(summary) == 243


def test_classify_llm_error_detects_timeout_and_rate_limit():
    assert classify_llm_error(TimeoutError()) == "timeout"
    assert classify_llm_error(RuntimeError("429 Too Many Requests")) == "rate_limited"
    assert classify_llm_error(RuntimeError("boom")) == "error"


def test_build_chat_completion_kwargs_disables_thinking_for_direct_deepseek():
    kwargs = build_chat_completion_kwargs(
        model_name="deepseek-v4-pro",
        system_prompt="base prompt",
        anonymized_text="User_1: hello",
        max_output_tokens=1200,
        provider="DeepSeek API",
    )

    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_build_chat_completion_kwargs_can_enable_thinking_for_direct_deepseek():
    kwargs = build_chat_completion_kwargs(
        model_name="deepseek-v4-pro",
        system_prompt="base prompt",
        anonymized_text="User_1: hello",
        max_output_tokens=2400,
        provider="DeepSeek API",
        thinking_mode="enabled",
    )

    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


def test_build_chat_completion_kwargs_keeps_openrouter_without_deepseek_extra_body():
    kwargs = build_chat_completion_kwargs(
        model_name="deepseek/deepseek-v4-flash",
        system_prompt="base prompt",
        anonymized_text="User_1: hello",
        max_output_tokens=1200,
        provider="OpenRouter",
    )

    assert "extra_body" not in kwargs


def test_build_chunk_chat_completion_kwargs_disables_thinking_for_direct_deepseek():
    kwargs = build_chunk_chat_completion_kwargs(
        model_name="deepseek-v4-flash",
        system_prompt="chunk prompt",
        chunk_payload={"messages": []},
        provider="DeepSeek API",
    )

    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_generate_chunk_summary_parses_json_payload():
    result = await generate_chunk_summary(
        FakeChunkClient(),
        "deepseek-v4-flash",
        {"messages": []},
        "chunk prompt",
        provider="DeepSeek API",
    )

    assert result.payload["topics"] == ["topic"]
    assert result.output_tokens == 12


def test_prepare_final_message_decodes_names_and_escapes_summary_html():
    anonymizer = Anonymizer()
    anonymizer._get_fake_name("Alice")

    final_text = prepare_final_message("<b>User_1 [советует]</b> wins", anonymizer)

    assert "&lt;b&gt;Alice&lt;/b&gt; wins" in final_text
    assert "<b>Alice</b>" not in final_text
    assert final_text.endswith("</i>")


def test_prepare_final_message_adds_experiment_notice():
    anonymizer = Anonymizer()

    final_text = prepare_final_message(
        "User_1 wins",
        anonymizer,
        experiment_notice="Тестовый ответ: оцени ниже, стало лучше или хуже.",
    )

    assert (
        "\n\n—\n"
        "Тестовый ответ: оцени ниже, стало лучше или хуже.\n"
        "<i>Это сообщение сгенерировано ИИ и может быть неточным.</i>"
    ) in final_text
    assert final_text.endswith("</i>")


def test_strip_role_tags_from_summary_keeps_filtered_blocks():
    assert strip_role_tags_from_summary("User_1 [советует] и [FILTERED]") == "User_1 и [FILTERED]"
    assert strip_role_tags_from_summary("User_1 [FILTERED]") == "User_1 [FILTERED]"
    assert strip_role_tags_from_summary("User_2 [отвечает User_1] ответил") == "User_2 ответил"
    assert strip_role_tags_from_summary("User_2 [в ответ User_1] ответил") == "User_2 ответил"


@pytest.mark.asyncio
async def test_generate_summary_calls_llm_with_anonymized_context():
    client = FakeClient()
    observed = []
    prepared_context = PreparedSummaryContext(
        rendered_text="User_1 [инициатор]: hello [EMAIL]",
        raw_message_count=2,
        turn_count=1,
        merged_count=1,
    )
    
    original_observer = llm.observe_llm_request
    llm.observe_llm_request = lambda *args, **kwargs: observed.append((args, kwargs))
    try:
        result = await generate_summary(
            client,
            "test-model",
            prepared_context,
            "base prompt",
            chat_id=42,
        )
    finally:
        llm.observe_llm_request = original_observer

    assert result is not None
    assert result.text == "User_1 said hi"
    assert result.model_name == "test-model"
    assert result.input_tokens == 12
    assert result.output_tokens == 5
    assert result.anonymized_context == "User_1 [инициатор]: hello [EMAIL]"
    assert len(observed) == 1
    args, kwargs = observed[0]
    assert args[0] == "success"
    assert isinstance(args[1], float)
    assert args[1] >= 0
    assert kwargs == {"input_tokens": 12, "output_tokens": 5}

    call = client.completions.calls[0]
    assert call["model"] == "test-model"
    assert call["max_tokens"] == SUMMARY_SHORT_LOG_MAX_OUTPUT_TOKENS
    assert call["messages"][0]["role"] == "system"
    assert "User_1 [инициатор]: hello [EMAIL]" in call["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_summary_uses_explicit_max_output_tokens_and_thinking_mode():
    client = FakeClient()

    result = await generate_summary(
        client,
        "deepseek-v4-pro",
        PreparedSummaryContext(
            rendered_text="User_1 [инициатор]: hello",
            raw_message_count=1,
            turn_count=1,
            merged_count=0,
        ),
        "base prompt",
        chat_id=42,
        provider="DeepSeek API",
        max_output_tokens_override=2400,
        thinking_mode="enabled",
    )

    assert result is not None
    call = client.completions.calls[0]
    assert call["max_tokens"] == 2400
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.asyncio
async def test_generate_summary_retries_empty_response(monkeypatch):
    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    client = FakeEmptyThenSuccessClient()

    result = await generate_summary(
        client,
        "test-model",
        PreparedSummaryContext(
            rendered_text="User_1 [инициатор]: hello",
            raw_message_count=1,
            turn_count=1,
            merged_count=0,
        ),
        "base prompt",
        chat_id=42,
    )

    assert result is not None
    assert result.text == "User_1 recovered"
    assert len(client.completions.calls) == 2


@pytest.mark.asyncio
async def test_generate_summary_regenerates_invalid_output_once():
    client = FakeInvalidThenSuccessClient()

    result = await generate_summary(
        client,
        "test-model",
        PreparedSummaryContext(
            rendered_text="User_1 [инициатор]: Redis упал",
            raw_message_count=1,
            turn_count=1,
            merged_count=0,
        ),
        "base prompt",
        chat_id=42,
    )

    assert result is not None
    assert result.text == "User_1 починил Redis."
    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][0]["content"]
    assert "[VALIDATION RETRY]" in retry_prompt
    assert "Markdown" in retry_prompt


@pytest.mark.asyncio
async def test_generate_summary_stops_after_one_validation_retry():
    client = FakeAlwaysInvalidClient()

    result = await generate_summary(
        client,
        "test-model",
        PreparedSummaryContext(
            rendered_text="User_1 [инициатор]: Redis упал",
            raw_message_count=1,
            turn_count=1,
            merged_count=0,
        ),
        "base prompt",
        chat_id=42,
    )

    assert result is None
    assert len(client.completions.calls) == 2


@pytest.mark.asyncio
async def test_generate_summary_rejects_token_limit_truncation():
    client = FakeTruncatedClient()
    observed = []

    original_observer = llm.observe_llm_request
    llm.observe_llm_request = lambda *args, **kwargs: observed.append((args, kwargs))
    try:
        result = await generate_summary(
            client,
            "deepseek-v4-pro",
            PreparedSummaryContext(
                rendered_text="User_1 [инициатор]: hello",
                raw_message_count=1,
                turn_count=1,
                merged_count=0,
            ),
            "base prompt",
            chat_id=42,
            provider="DeepSeek API",
        )
    finally:
        llm.observe_llm_request = original_observer

    assert result is None
    assert len(client.completions.calls) == 1
    assert client.completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert observed[0][0][0] == "truncated"
    assert observed[0][1] == {"input_tokens": 100, "output_tokens": 1200}


@pytest.mark.asyncio
async def test_generate_summary_skips_request_when_deadline_is_exhausted(monkeypatch):
    monkeypatch.setattr(llm.time, "monotonic", lambda: 100.0)
    client = FakeClient()

    result = await generate_summary(
        client,
        "test-model",
        PreparedSummaryContext(
            rendered_text="User_1 [инициатор]: hello",
            raw_message_count=1,
            turn_count=1,
            merged_count=0,
        ),
        "base prompt",
        chat_id=42,
        deadline=101.0,
    )

    assert result is None
    assert client.completions.calls == []


@pytest.mark.asyncio
async def test_should_retry_after_error_skips_sleep_when_deadline_would_expire(monkeypatch):
    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(llm.time, "monotonic", lambda: 100.0)

    should_retry = await should_retry_after_error(
        TimeoutError(),
        attempt=1,
        delay=15,
        deadline=110.0,
    )

    assert should_retry is False
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_generate_summary_uses_dynamic_example_in_system_prompt():
    client = FakeClient()
    dynamic_example = SummaryExample(
        summary_log_id=33,
        input_log="User_1: тильтанул",
        ideal_summary="User_1 красиво тильтанул.",
    )

    result = await generate_summary(
        client,
        "test-model",
        PreparedSummaryContext(
            rendered_text="User_1 [инициатор]: hello",
            raw_message_count=1,
            turn_count=1,
            merged_count=0,
        ),
        "base\n<example>static</example>",
        chat_id=42,
        dynamic_example=dynamic_example,
    )

    assert result is not None
    system_prompt = client.completions.calls[0]["messages"][0]["content"]
    assert "<example>static</example>" not in system_prompt
    assert "User_1: тильтанул" in system_prompt
    assert "User_1 красиво тильтанул." in system_prompt
