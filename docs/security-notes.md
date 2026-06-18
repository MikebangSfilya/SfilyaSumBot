# Security and Privacy Notes

SumBot reduces exposure before sending chat text to an LLM, but it is not a formal anonymization or compliance system.

## Data Flow

- Telegram messages are stored in Redis as a short-term working buffer.
- `/summary` renders recent chat context, anonymizes names and common PII, and sends the anonymized text to the configured LLM provider.
- Generated summaries and anonymized source context may be stored in PostgreSQL analytics.
- Feedback and optional feedback details may also be stored in PostgreSQL.

## Important Limitations

- Anonymization is best-effort. It can miss unusual names, handles, links, or identifying context.
- The anonymizer is regex- and metadata-based, not a guaranteed de-identification engine. It masks known authors, usernames, `@mentions`, `http(s)` URLs, emails, and some phone formats, but it does not understand every language, nickname, bare domain, address, document number, bank/card pattern, or identifying story detail.
- Names mentioned inside the message body are not generally replaced unless they appear as known Telegram metadata or `@mentions`.
- Phone masking is intentionally narrow. Non-RU/US-like formats, numbers split across words, or IDs that look similar to numbers can be missed or over-masked.
- URLs without `http://` or `https://`, invite links written as plain text, screenshots, files, stickers, voice messages, and images are outside the current text anonymization path.
- Short or highly specific chats can stay identifiable after anonymization through context alone, even when names are replaced with `User_1`, `User_2`, etc.
- LLM providers still receive the anonymized text and should be treated as external data processors.
- The fallback chain can send the same anonymized context to more than one configured LLM provider if earlier attempts fail.
- Redis history is temporary, but it can still contain sensitive chat text while retained.
- Redis stores original message text, Telegram user IDs, usernames, first names, reply metadata, and message IDs. The code trims lists by count, but it does not set a TTL on chat history keys.
- If chunking is enabled, Redis also stores active chunk payloads with original message text until the chunk is summarized or fails. Saved chunk summaries keep participant names/usernames and LLM-compressed event text.
- PostgreSQL analytics intentionally stores anonymized context and summaries for quality analysis. Configure `SUMMARY_LOG_RETENTION_LIMIT` to limit retained rows.
- Feedback details are stored as user-written text and are not anonymized before saving.
- `bot_chats` stores chat IDs, titles, usernames, public links, and bot status. This is operational metadata, not anonymous analytics.
- Dynamic examples reuse previous "good" summaries and anonymized contexts inside future prompts when `SUMMARY_DYNAMIC_EXAMPLES_ENABLED=true`. Keep it off unless this reuse is acceptable.
- Telegram Bot API does not let the bot read old chat history. The bot only sees messages delivered after it joins or while it is active.
- Telegram Bot API cannot recursively delete every old bot message from a chat unless the bot saved the relevant `message_id` values and Telegram still allows deletion.
- Owner/debug actions depend on `DEBUG_USER_ID`. Set it explicitly before running a public bot.
- `DEBUG_USER_ID=0` effectively disables legitimate owner access, but a wrong real ID gives that account cross-chat debug/admin controls.
- Docker Compose keeps Redis, PostgreSQL, and Grafana data in persistent volumes. Removing containers is not the same as deleting stored chat data.
- `.env` files are ignored by git, but exported analytics files (`*.json`, `*.csv`, archives) are also ignored and can remain on disk unnoticed.

## Operational Pitfalls for Maintainers

- Do not describe this project as "anonymous" or "GDPR-ready". The accurate claim is "best-effort PII reduction before LLM calls".
- Do not assume `/summary` only touches one provider. Check `OPENROUTER_*`, `DEEPSEEK_*`, `LLM_DEFAULT_MODEL_ID`, `SHORT_LOG_MODEL_ID`, and fallback behavior.
- Do not publish a live database, Redis dump, Docker volume, Jaeger trace export, Grafana snapshot, or analytics export without manual review.
- Do not enable chunking or dynamic examples in sensitive chats unless the extra retention and prompt reuse are acceptable.
- Keep `LOG_LEVEL=INFO` or higher in shared environments. Debug logs can expose operational metadata and sometimes snippets/errors from provider responses.
- Review migrations and tools before renaming `raw_context`; scripts and reports currently depend on that legacy column name.

## Recommended Public Deployment Hygiene

- Keep `DAILY_DIGEST_DEFAULT_ENABLED=false` until groups explicitly opt in.
- Review prompts and stored analytics before enabling dynamic examples.
- If publishing a formerly private project, prefer a fresh public repository from a sanitized snapshot instead of exposing private commit history.
- Rotate Telegram and LLM API keys before making any formerly private deployment public.
- Set `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `DEBUG_USER_ID`, retention limits, and provider keys explicitly. Do not rely on example defaults.
