# SumBot

SumBot is a Telegram bot that keeps a short Redis-backed chat history and generates `/summary` digests with an OpenAI-compatible LLM. It anonymizes chat participants before sending context to the model, stores compact analytics in PostgreSQL, and exposes optional Prometheus/Grafana monitoring.

Russian documentation: [README.ru.md](README.ru.md).

## Features

- `/summary` for recent messages and `/summary N` for the last `N` messages.
- Optional daily digests that group admins can disable with `/digest off`.
- Prompt profile controls for summary style, tone, and aggressiveness.
- PII/user anonymization before LLM calls, with names restored in the final Telegram message.
- Inline feedback buttons and analytics for model quality, latency, token use, and feedback.
- Redis chat buffer, PostgreSQL analytics, Alembic migrations, and Docker Compose runtime.
- Optional Prometheus metrics, Grafana dashboard, and OpenTelemetry traces.

## Project Background

SumBot started as a private Telegram chat summarizer and later grew into a small observability-heavy bot with feedback analytics, prompt profiles, daily digests, and monitoring. This public release is a sanitized snapshot: private runtime files, local notes, and credentials are intentionally excluded.

See [docs/project-history.md](docs/project-history.md) for the public development timeline and [docs/security-notes.md](docs/security-notes.md) for privacy and security limitations.

## Quick Start

Requirements:

- Python 3.11+
- Docker and Docker Compose
- [uv](https://docs.astral.sh/uv/)
- [Task](https://taskfile.dev/) optional, but used by the shortcuts below

```bash
cp .env.example .env
# Fill TG_TOKEN and at least one LLM API key in .env.
task up
task logs:bot
```

The bot container applies Alembic migrations before starting polling.

Without Task:

```bash
docker compose --env-file .env -p sumbot up -d --build
docker compose --env-file .env -p sumbot logs -f bot
```

## Configuration

Main variables in `.env`:

| Variable | Required | Purpose |
| --- | --- | --- |
| `TG_TOKEN` | Yes | Telegram bot token from BotFather. |
| `LLM_API_KEY` or `OPENROUTER_API_KEY` | Yes for OpenRouter | OpenRouter/OpenAI-compatible API key. |
| `DEEPSEEK_API_KEY` | Optional | Direct DeepSeek API key. |
| `DATABASE_URL` | Yes for Docker | Async SQLAlchemy PostgreSQL URL. |
| `REDIS_HOST` | No | Redis host, usually `redis` in Compose. |
| `DEBUG_USER_ID` | Recommended | Telegram user ID allowed to use owner-only debug/admin commands. |
| `ANALYTICS_CHAT_ID` | Optional | Telegram chat for analytics reports and join notifications. |
| `OPENROUTER_MODEL`, `OPENROUTER_MODELS` | Optional | OpenRouter model catalog. |
| `LLM_DEFAULT_MODEL_ID` | Optional | Default model id such as `openrouter:deepseek/deepseek-v4-flash`. |
| `DAILY_DIGEST_*` | Optional | Daily digest scheduler settings. |
| `METRICS_*`, `TRACING_*` | Optional | Monitoring and tracing settings. |

## Development

```bash
task sync
task test
task check
```

Useful Docker commands:

```bash
task up
task ps
task logs:bot
task migrate
task down
```

Focused tests:

```bash
uv run pytest tests/test_services.py -q
```

## Architecture

High-level flow:

```text
Telegram update -> aiogram handlers -> Redis chat history
  -> anonymizer -> LLM -> Telegram summary
  -> PostgreSQL analytics and feedback
```

More details:

- [docs/architecture.md](docs/architecture.md)
- [docs/monitoring.md](docs/monitoring.md)

Project layout:

```text
sumbot/          Bot runtime package and Telegram handlers
tools/analytics/ Analytics export, reporting, cleanup, and delivery CLIs
scripts/         Standalone diagnostics and benchmarking helpers
prompts/         Prompt profile catalogs
migrations/      Alembic migrations
monitoring/      Prometheus and Grafana config
tests/           Pytest suite
```

## Privacy Notes

SumBot anonymizes user names, usernames, links, emails, and phone-like values before LLM requests. PostgreSQL analytics still store anonymized source context and generated summaries for quality analysis. Configure retention with `SUMMARY_LOG_RETENTION_LIMIT`.

Read the full limitations in [docs/security-notes.md](docs/security-notes.md).

## License

MIT. See [LICENSE](LICENSE).
