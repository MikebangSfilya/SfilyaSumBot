# Security and Privacy Notes

SumBot reduces exposure before sending chat text to an LLM, but it is not a formal anonymization or compliance system.

## Data Flow

- Telegram messages are stored in Redis as a short-term working buffer.
- `/summary` renders recent chat context, anonymizes names and common PII, and sends the anonymized text to the configured LLM provider.
- Generated summaries and anonymized source context may be stored in PostgreSQL analytics.
- Feedback and optional feedback details may also be stored in PostgreSQL.

## Important Limitations

- Anonymization is best-effort. It can miss unusual names, handles, links, or identifying context.
- LLM providers still receive the anonymized text and should be treated as external data processors.
- Redis history is temporary, but it can still contain sensitive chat text while retained.
- PostgreSQL analytics intentionally stores anonymized context and summaries for quality analysis. Configure `SUMMARY_LOG_RETENTION_LIMIT` to limit retained rows.
- Telegram Bot API does not let the bot read old chat history. The bot only sees messages delivered after it joins or while it is active.
- Telegram Bot API cannot recursively delete every old bot message from a chat unless the bot saved the relevant `message_id` values and Telegram still allows deletion.
- Owner/debug actions depend on `DEBUG_USER_ID`. Set it explicitly before running a public bot.
## Recommended Public Deployment Hygiene

- Keep `DAILY_DIGEST_DEFAULT_ENABLED=false` until groups explicitly opt in.
- Review prompts and stored analytics before enabling dynamic examples.
- If publishing a formerly private project, prefer a fresh public repository from a sanitized snapshot instead of exposing private commit history.
