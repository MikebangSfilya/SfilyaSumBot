# Understanding Checklist: debug refactor

Commit: `9037822 refactor: split debug handlers by responsibility`

## Problem

- [x] Explain why the former `debug.py` had mixed responsibilities rather than merely too many lines.
- [x] Name the three user scenarios that were coupled in the former module.
- [ ] Explain the constraints of this refactor: unchanged behavior, unchanged tests, small reviewable steps, and low complexity.
- [x] Identify the risks of splitting an aiogram module: lost registration, changed callback routing, circular imports, and changed side effects.

## Solution

- [x] Explain the role of `registry.py` after the split.
- [x] Explain the handlers/panel/runtime separation and why dependency direction matters.
- [ ] Map the `/debug` scenario to `debug_handlers.py`, `debug_panel.py`, `debug_runtime.py`, and `debug_message_lifecycle.py`.
- [x] Map prompt-profile behavior to `prompt_profile_handlers.py` and `prompt_profile_panel.py`.
- [x] Map admin-chat behavior to `admin_chat_handlers.py`, `admin_chat_panel.py`, and `admin_chat_runtime.py`.
- [x] Explain why shared generation controls belong in `settings_panel.py` and callback constants in `debug_constants.py`.
- [x] Trace one command or callback from aiogram registration to service mutation and Telegram message refresh.
- [ ] Explain what early returns improve in these handlers without changing behavior.

## Behavior And Edge Cases

- [ ] Explain how authorization, malformed callback data, missing chat/message/bot context, and Telegram edit failures are handled.
- [ ] Explain why deleting the `debug.py` facade is useful and what import changes it requires.
- [ ] Identify at least one plausible regression caused by this split and the test or check that detects it.

## Broader Context

- [ ] Explain how the new boundaries make later changes safer and where a new debug setting should be implemented.
- [ ] Explain which files and public/import surfaces were affected.
- [ ] Explain what `149 passed`, Radon CC/MI `A`, compile checks, and the cycle audit each prove and do not prove.
- [ ] State the remaining repository condition: `pyproject.toml` and `uv.lock` are modified outside this commit.

## Verification Log

- [ ] The human restated the original design problem accurately.
- [x] The human explained the module boundaries and dependency direction accurately.
- [x] The human traced a concrete runtime path accurately.
- [ ] The human answered edge-case and regression questions.
- [ ] The human connected the refactor to maintenance and verification limits.

## Open Questions

- Session paused by the user to limit token usage.
- Resume from: benefits and risks of deleting the former `debug.py` import facade.
- Still to verify: `/debug` module mapping, early returns, edge cases, regression checks, verification limits, and broader maintenance impact.
