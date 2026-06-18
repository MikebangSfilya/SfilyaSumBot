import pytest

from sumbot.constants import (
    SUMMARY_DYNAMIC_EXAMPLE_MAX_CONTEXT_CHARS,
    SUMMARY_DYNAMIC_EXAMPLE_MAX_RESPONSE_CHARS,
    SUMMARY_LOG_COUNTER_NAME,
)
from sumbot.database import (
    _get_or_create_prompt_id,
    fetch_random_good_summary_example,
    find_summary_log_id,
    prune_old_summary_logs,
    save_summary_analytics,
    summary_feedback_exists,
    update_summary_feedback_details,
    upsert_summary_feedback,
)
from sumbot.prompt_builder import build_presentation_settings, load_style_catalog, load_tone_catalog


def _presentation():
    return build_presentation_settings(
        load_style_catalog(),
        load_tone_catalog(),
        style_id="executive_brief",
        tone_id="dry",
        aggressiveness=0,
    )


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        row = self.rows.pop(0) if self.rows else None
        return FakeResult(row)


class FakeFeedbackUpsertConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.feedback_rows = {}

    async def execute(self, statement, params=None):
        await super().execute(statement, params)
        key = (params["chat_id"], params["message_id"], params["user_id"])
        self.feedback_rows[key] = {
            "summary_log_id": params["summary_log_id"],
            "feedback_value": params["feedback_value"],
            "sentiment": params["sentiment"],
        }
        return FakeResult()


class FakeBegin:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return FakeBegin(self.conn)


@pytest.mark.asyncio
async def test_get_or_create_prompt_id_returns_existing_prompt():
    conn = FakeConnection(rows=[(7,)])

    prompt_id = await _get_or_create_prompt_id(conn, "system prompt")

    assert prompt_id == 7
    assert len(conn.calls) == 1
    assert conn.calls[0][1] == {"prompt": "system prompt"}


@pytest.mark.asyncio
async def test_get_or_create_prompt_id_inserts_when_missing():
    conn = FakeConnection(rows=[None, (11,)])

    prompt_id = await _get_or_create_prompt_id(conn, "new prompt")

    assert prompt_id == 11
    assert len(conn.calls) == 2
    assert "INSERT INTO prompts" in conn.calls[1][0]
    assert conn.calls[1][1] == {"prompt": "new prompt"}


@pytest.mark.asyncio
async def test_find_summary_log_id_returns_row_id():
    conn = FakeConnection(rows=[(42,)])

    summary_log_id = await find_summary_log_id(conn, chat_id=100, telegram_message_id=200)

    assert summary_log_id == 42
    assert conn.calls[0][1] == {"chat_id": 100, "message_id": 200}


@pytest.mark.asyncio
async def test_fetch_random_good_summary_example_returns_good_example():
    conn = FakeConnection(rows=[(33, " User_1: выдал базу ", " User_1 разнес чат. ")])
    engine = FakeEngine(conn)

    example = await fetch_random_good_summary_example(engine, _presentation())

    assert example is not None
    assert example.summary_log_id == 33
    assert example.input_log == "User_1: выдал базу"
    assert example.ideal_summary == "User_1 разнес чат."

    statement, params = conn.calls[0]
    assert "FROM summary_logs sl" in statement
    assert "summary_feedback sf" in statement
    assert "sf.feedback_value = :feedback_value" in statement
    assert "ORDER BY RANDOM()" in statement
    assert params == {
        "feedback_value": "good",
        "max_context_chars": SUMMARY_DYNAMIC_EXAMPLE_MAX_CONTEXT_CHARS,
        "max_response_chars": SUMMARY_DYNAMIC_EXAMPLE_MAX_RESPONSE_CHARS,
        "style_id": "executive_brief",
        "tone_id": "dry",
        "aggressiveness": 0,
    }


@pytest.mark.asyncio
async def test_fetch_random_good_summary_example_returns_none_without_db():
    assert await fetch_random_good_summary_example(None, _presentation()) is None


@pytest.mark.asyncio
async def test_fetch_random_good_summary_example_returns_none_when_empty():
    conn = FakeConnection(rows=[None])
    engine = FakeEngine(conn)

    assert await fetch_random_good_summary_example(engine, _presentation()) is None


@pytest.mark.asyncio
async def test_upsert_summary_feedback_executes_expected_params():
    conn = FakeConnection()

    await upsert_summary_feedback(
        conn,
        summary_log_id=1,
        chat_id=2,
        telegram_message_id=3,
        user_id=4,
        feedback_value="good",
        sentiment="positive",
    )

    statement, params = conn.calls[0]
    assert "INSERT INTO summary_feedback" in statement
    assert "ON CONFLICT" in statement
    assert params == {
        "summary_log_id": 1,
        "chat_id": 2,
        "message_id": 3,
        "user_id": 4,
        "feedback_value": "good",
        "sentiment": "positive",
    }


@pytest.mark.asyncio
async def test_repeated_summary_feedback_clicks_keep_single_logical_feedback_row():
    conn = FakeFeedbackUpsertConnection()
    reactions = [
        ("good", "positive"),
        ("neutral", "neutral"),
        ("bad", "negative"),
    ]

    for index in range(100):
        feedback_value, sentiment = reactions[index % len(reactions)]
        await upsert_summary_feedback(
            conn,
            summary_log_id=1,
            chat_id=2,
            telegram_message_id=3,
            user_id=4,
            feedback_value=feedback_value,
            sentiment=sentiment,
        )

    assert len(conn.calls) == 100
    assert len(conn.feedback_rows) == 1
    assert conn.feedback_rows[(2, 3, 4)] == {
        "summary_log_id": 1,
        "feedback_value": "good",
        "sentiment": "positive",
    }

    statement, _params = conn.calls[0]
    assert "ON CONFLICT (chat_id, telegram_message_id, user_id)" in statement
    assert "DO UPDATE SET" in statement


@pytest.mark.asyncio
async def test_update_summary_feedback_details_updates_expected_row():
    conn = FakeConnection(rows=[(9,)])

    saved = await update_summary_feedback_details(
        conn,
        chat_id=2,
        telegram_message_id=3,
        user_id=4,
        details="слишком длинно и не понял контекст",
    )

    statement, params = conn.calls[0]
    assert saved is True
    assert "UPDATE summary_feedback" in statement
    assert "details_updated_at = CURRENT_TIMESTAMP" in statement
    assert params == {
        "chat_id": 2,
        "message_id": 3,
        "user_id": 4,
        "details": "слишком длинно и не понял контекст",
    }


@pytest.mark.asyncio
async def test_summary_feedback_exists_checks_expected_row():
    conn = FakeConnection(rows=[(1,)])

    exists = await summary_feedback_exists(
        conn,
        chat_id=2,
        telegram_message_id=3,
        user_id=4,
    )

    statement, params = conn.calls[0]
    assert exists is True
    assert "FROM summary_feedback" in statement
    assert "LIMIT 1" in statement
    assert params == {
        "chat_id": 2,
        "message_id": 3,
        "user_id": 4,
    }


@pytest.mark.asyncio
async def test_save_summary_analytics_gets_prompt_and_inserts_summary_log():
    conn = FakeConnection(rows=[None, (5,), None, None, None])
    engine = FakeEngine(conn)

    await save_summary_analytics(
        engine,
        chat_id=10,
        telegram_message_id=20,
        system_prompt="system prompt",
        anonymized_context="context",
        anonymized_response="summary",
        model_name="model",
        input_tokens=100,
        output_tokens=50,
        summary_duration_seconds=4.5,
        llm_duration_seconds=3.25,
        presentation_settings=_presentation(),
        trigger_source="daily_digest",
    )

    assert len(conn.calls) == 5
    counter_statement, counter_params = conn.calls[2]
    assert "INSERT INTO analytics_counters" in counter_statement
    assert counter_params == {"name": SUMMARY_LOG_COUNTER_NAME}

    statement, params = conn.calls[3]
    assert "INSERT INTO summary_logs" in statement
    assert params == {
        "chat_id": 10,
        "prompt_id": 5,
        "model_name": "model",
        "raw_context": "context",
        "llm_response": "summary",
        "input_tokens": 100,
        "output_tokens": 50,
        "summary_duration_seconds": 4.5,
        "llm_duration_seconds": 3.25,
        "style_id": "executive_brief",
        "tone_id": "dry",
        "aggressiveness": 0,
        "trigger_source": "daily_digest",
        "telegram_message_id": 20,
    }

    prune_statement, prune_params = conn.calls[4]
    assert "DELETE FROM summary_logs" in prune_statement
    assert prune_params == {"retention_limit": 200}


@pytest.mark.asyncio
async def test_prune_old_summary_logs_skips_invalid_limit():
    conn = FakeConnection()

    pruned_rows = await prune_old_summary_logs(conn, retention_limit=0)

    assert pruned_rows == 0
    assert conn.calls == []
