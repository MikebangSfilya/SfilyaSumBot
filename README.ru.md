# SumBot

SumBot - Telegram-бот, который хранит короткую историю чата в Redis и делает пересказы командой `/summary` через OpenAI-compatible LLM. Перед отправкой контекста в модель бот анонимизирует участников, сохраняет компактную аналитику в PostgreSQL и может отдавать метрики в Prometheus/Grafana.

English documentation: [README.md](README.md).

## Возможности

- `/summary` для свежих сообщений и `/summary N` для последних `N` сообщений.
- Опциональные ежедневные дайджесты, которые администратор группы может отключить через `/digest off`.
- Настройки профиля пересказа: стиль, тон и агрессивность.
- Анонимизация PII и участников перед LLM-запросом, с обратной подстановкой имен в финальном сообщении.
- Inline feedback и аналитика качества, задержек, токенов и моделей.
- Redis-буфер чата, PostgreSQL analytics, Alembic migrations и Docker Compose runtime.
- Опциональные Prometheus metrics, Grafana dashboard и OpenTelemetry traces.

## История проекта

SumBot начинался как приватный Telegram-бот для пересказа активных чатов, а потом вырос в небольшой observability-heavy проект с feedback analytics, prompt profiles, daily digests и мониторингом. Публичная версия - очищенный snapshot: приватные runtime-файлы, локальные заметки и credentials намеренно исключены.

Публичная хронология: [docs/project-history.md](docs/project-history.md). Ограничения приватности и безопасности: [docs/security-notes.md](docs/security-notes.md).

## Быстрый старт

Нужно:

- Python 3.11+
- Docker и Docker Compose
- [uv](https://docs.astral.sh/uv/)
- [Task](https://taskfile.dev/) для коротких команд ниже

```bash
cp .env.example .env
# Заполни TG_TOKEN и минимум один LLM API key в .env.
task up
task logs:bot
```

Контейнер бота применяет Alembic-миграции перед стартом polling.

Без Task:

```bash
docker compose --env-file .env -p sumbot up -d --build
docker compose --env-file .env -p sumbot logs -f bot
```

## Конфигурация

Основные переменные в `.env`:

| Переменная | Обязательна | Назначение |
| --- | --- | --- |
| `TG_TOKEN` | Да | Токен Telegram-бота из BotFather. |
| `LLM_API_KEY` или `OPENROUTER_API_KEY` | Да для OpenRouter | API key для OpenRouter/OpenAI-compatible endpoint. |
| `DEEPSEEK_API_KEY` | Нет | Ключ прямого DeepSeek API. |
| `DATABASE_URL` | Да для Docker | Async SQLAlchemy URL PostgreSQL. |
| `REDIS_HOST` | Нет | Host Redis, обычно `redis` в Compose. |
| `DEBUG_USER_ID` | Желательно | Telegram user ID для owner-only debug/admin команд. |
| `ANALYTICS_CHAT_ID` | Нет | Telegram-чат для аналитики и уведомлений о новых чатах. |
| `OPENROUTER_MODEL`, `OPENROUTER_MODELS` | Нет | Каталог OpenRouter моделей. |
| `LLM_DEFAULT_MODEL_ID` | Нет | Default model id, например `openrouter:deepseek/deepseek-v4-flash`. |
| `DAILY_DIGEST_*` | Нет | Настройки ежедневного дайджеста. |
| `METRICS_*`, `TRACING_*` | Нет | Метрики и трассировка. |

## Разработка

```bash
task sync
task test
task check
```

Полезные Docker-команды:

```bash
task up
task ps
task logs:bot
task migrate
task down
```

Точечный тест:

```bash
uv run pytest tests/test_services.py -q
```

## Архитектура

Основной поток:

```text
Telegram update -> aiogram handlers -> Redis chat history
  -> anonymizer -> LLM -> Telegram summary
  -> PostgreSQL analytics and feedback
```

Подробнее:

- [docs/architecture.md](docs/architecture.md)
- [docs/monitoring.md](docs/monitoring.md)

Структура проекта:

```text
sumbot/          Runtime-код бота и Telegram handlers
tools/analytics/ CLI для analytics export/report/cleanup/send
scripts/         Отдельные диагностики и benchmark helpers
prompts/         Каталоги prompt profiles
migrations/      Alembic migrations
monitoring/      Конфиги Prometheus и Grafana
tests/           Pytest suite
```

## Приватность

SumBot анонимизирует имена, username, ссылки, email и похожие на телефоны значения перед LLM-запросом. PostgreSQL analytics при этом хранит анонимизированный контекст и сгенерированные пересказы для анализа качества. Retention настраивается через `SUMMARY_LOG_RETENTION_LIMIT`.

Полный список ограничений: [docs/security-notes.md](docs/security-notes.md).

## Лицензия

MIT. См. [LICENSE](LICENSE).
