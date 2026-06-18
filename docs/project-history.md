# Project History

SumBot started as a private Telegram chat summarizer and was later prepared for public release as a sanitized open-source snapshot.

## Timeline

- **Initial bot**: collected recent chat messages and generated `/summary` responses through an OpenAI-compatible LLM.
- **Privacy pass**: added participant and PII anonymization before LLM requests, with restored display names in Telegram responses.
- **Analytics loop**: added PostgreSQL storage for summaries, token usage, model metadata, and feedback.
- **Feedback controls**: added inline feedback buttons and detailed negative feedback collection.
- **Prompt profiles**: added per-chat style, tone, and aggressiveness controls through Telegram panels.
- **Digest workflow**: added optional daily digest scheduling with per-chat opt-out.
- **Observability**: added Prometheus metrics, Grafana dashboards, and optional OpenTelemetry tracing.
- **Open-source cleanup**: removed private runtime files, internal planning notes, local IDE state, one-off shutdown tools, and personal defaults.

## Current Shape

The public repository is intended to be a maintainable example of a Telegram summarization bot with:

- explicit async Telegram handling;
- Redis as short-term chat memory;
- PostgreSQL analytics and Alembic migrations;
- LLM fallback/model selection logic;
- monitoring and reporting utilities.

Public development starts from the sanitized snapshot; earlier private iteration is summarized here instead of being exposed as raw commit history.
