# Changelog

Meaningful product and operational changes are recorded here, newest first.

## 2026-06-18

- Prepared a sanitized open-source snapshot with MIT licensing, bilingual README files, public project history and security notes.
- Moved analytics command-line utilities into `tools/analytics` to keep the repository root focused on app entrypoints and configuration.
- Removed private runtime files, internal planning notes, local IDE state, one-off shutdown tools and personal defaults from the public tree.
- Added analytics outlier summaries with confidence labels and explicit unavailable fallback, validator and chunk-worker failure signals.
- Added `SHORT_LOG_MODEL_ID` for short-summary model canaries, with normal LLM fallback and analytics cost estimates.
- Added `summary_logs.trigger_source` so reports can split manual `/summary` requests from automatic daily digests.
- Fixed chunk summary parsing when an LLM wraps valid JSON in a Markdown `json` fence.
- Fixed `/debug` line breaks and moved the enabled-chunking chat count into `/debug_chat_settings`.

## 2026-06-17

- Added a benchmark script for replaying negatively rated contexts against candidate models.
- Categorized negative feedback details and surfaced category statistics in analytics reports.
- Increased summary feedback keyboard lifetime to 24 hours.

## 2026-06-12

- Added group-admin `/prompt` controls for per-chat summary style, tone and aggressiveness.
- Added owner-triggered prompt-setting announcements for active groups.
- Added pre-delivery summary validation with one corrective regeneration before model fallback.
- Added optional dynamic examples from positively rated summaries, disabled by default.
- Persisted presentation dimensions in summary analytics and matched dynamic examples to the same presentation settings.
- Replaced raw chat IDs with registered chat titles in user-facing analytics reports.

## 2026-06-11

- Added Telegram delivery for analytics reports with KPI summary, PNG dashboard and full report attachments.
- Fixed analytics report filters under PostgreSQL asyncpg.
- Added quality, feedback coverage, latency, token, model, chat, day and hour breakdowns.
- Persisted summary and LLM durations for feedback correlation.
- Added configurable OpenRouter model catalogs and fixed chunk-model registration for arbitrary OpenRouter model IDs.
- Added back navigation and explicit removal confirmation in the admin chat panel.

## 2026-06-10

- Added optional daily digests with activation onboarding and administrator opt-out.
- Added daily digest scheduling controls and suppression after recent manual summaries.
- Fixed Telegram `BUTTON_DATA_INVALID` errors in chat admin callbacks.
- Added project changelog and current-status documentation during private development.

## 2026-06-07

- Added prompt profile catalogs and inline profile panels.
- Added chat join alerts and chat safety controls.
- Split debug handlers, keyboard builders and runtime helpers by responsibility.
- Added feedback detail updates.
- Merged Prometheus/Grafana monitoring setup.

## 2026-06-04

- Added chunk creation and context assembly improvements for larger chat histories.
- Improved cleanup of temporary Telegram messages.

## 2026-06-02

- Added Jaeger trace export tooling and tracing task helpers.

## 2026-06-01

- Added per-chat LLM model selection.
- Added LLM provider selection, fallback chains and degradation behavior.
- Limited expensive/pro models to explicit chat selection.
- Persisted Redis data across deploys.
- Tuned summary fallback timeout budgets and prevented truncated DeepSeek summaries.
- Added reminder exclusions and pre-model filtering.

## 2026-05-26

- Added bot chat registry coverage.
- Added guarded chat update reminders.
- Improved summary context attribution.

## 2026-05-23

- Added Grafana dashboards and monitoring port configuration.
- Refreshed project documentation around monitoring.

## 2026-05-22

- Added Prometheus metrics and Grafana monitoring.
- Improved anonymization and context preparation.
- Added analytics replay/export workflows.
- Added feedback collection, feedback auto-delete behavior and analytics cleanup.
- Fixed database migrations and test workflows.

## 2026-05-21

- Added summary feedback analytics.
- Added debug chat discovery and tests.
- Refactored feedback and documentation flows.

## 2026-05-19

- Added summary feedback analytics workflow.
- Removed tracked Python cache files from the private tree.

## 2026-05-01

- Added a new anonymizer pipeline.
- Added `/summary N` argument parsing for message-count summaries.
- Fixed asyncio blocking issues around summary handling.

## 2026-04-30

- Reduced noisy message logging.
- Hardened token/cache handling.
- Removed an older 8B model path.

## 2026-04-28

- Extracted summary logic into helper functions.
- Moved configuration and anonymization into separate modules.
- Added summary rate limiting and a lock to prevent command spam.
- Added debug command restrictions and database status output.
- Added logging for rate limits, summary generation and failures.
- Updated system prompt and model configuration.

## 2026-04-24

- Added analytics export foundations.
- Added initial anonymized summary datasets during private experimentation.

## 2026-04-23

- Added database migrations and logging setup.
- Iterated on human-readable prompts and message formatting.

## 2026-04-21

- Added timestamps, automatic message cleanup and early context expansion.

## 2026-04-18

- Initial private prototype.
