# Changelog

Meaningful product and operational changes are recorded here, newest first.

## 2026-06-18

- Moved analytics command-line utilities into `tools/analytics` to keep the repository root focused on app entrypoints and configuration.
- Prepared the repository for open-source release: removed private runtime files, internal notes and personal donation behavior, added MIT licensing, and sanitized public configuration defaults.
- Added analytics outlier summaries with confidence labels and explicit unavailable fallback/validator/chunk-worker failure signals.
- Added `SHORT_LOG_MODEL_ID` for a paid <=50-message summary canary with the normal LLM fallback chain behind it, plus analytics cost estimates against a Gemma baseline.
- Added `summary_logs.trigger_source` analytics so reports can split manual `/summary` requests from automatic daily digests.
- Fixed `/debug` message line breaks and moved the enabled-chunking chat count into `/debug_chat_settings`.
- Fixed chunk summary parsing when an LLM wraps valid JSON in a Markdown code fence.

## 2026-06-12

- Allowed Telegram group administrators to configure per-chat summary presentation through `/prompt`.
- Added an owner-triggered `/debug_announce_prompt` broadcast for active groups with exclude-list and cooldown protection.
- Added pre-delivery summary validation for broken markup and explicit unattributed political stance, with one corrective regeneration before model fallback.
- Added `SUMMARY_DYNAMIC_EXAMPLES_ENABLED`; positively rated few-shot examples are disabled by default in production and debug configuration.
- Added per-chat summary style, tone and aggressiveness controls to `/prompt`, `/debug` and chat administration.
- Persisted presentation dimensions in summary analytics and restricted dynamic examples to exact presentation matches.
- Split invariant summary rules from Markdown style/tone catalogs while preserving the previous default behavior.
- Replaced Telegram chat IDs with registered chat titles in text, CSV, JSON and visual analytics reports while preserving per-chat aggregation internally.

## 2026-06-11

- Added Telegram delivery for analytics reports with a concise KPI message, inline PNG dashboard and full text, JSON or CSV attachment.
- Fixed analytics reports with omitted model/chat filters under PostgreSQL asyncpg.
- Added actionable analytics reports with quality, feedback coverage, latency, token, model, chat, day and hour breakdowns.
- Persisted successful summary and LLM durations so performance can be correlated with user feedback.
- Added a configurable `OPENROUTER_MODELS` catalog and fixed standalone chunk-model registration so arbitrary OpenRouter models can be tested without silent DeepSeek fallback.
- Added back navigation, explicit removal confirmation, and a way to allow a previously removed bot to be added again.

## 2026-06-10

- Fixed Telegram `BUTTON_DATA_INVALID` errors in the chat admin panel by keeping callback data within the 64-byte limit.
- Added review acknowledgements to new-chat notifications without delaying bot startup.
- Added the ability to remove the bot later from a selected chat's admin panel; `left` is persisted only after Telegram confirms the exit.
