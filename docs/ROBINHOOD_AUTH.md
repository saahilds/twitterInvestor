# Robinhood authentication

Unofficial `robin_stocks` OAuth (not Robinhood’s Agentic MCP). Access tokens last a few days; refresh tokens last longer and rotate on use. Device approval / SMS is required when the refresh token dies — typically weekly or after a forced logout.

## Why reauth failed for several days (Nov 2026)

It was **not only** Robinhood rate limiting. Rate limits amplified bugs in our flow:

1. **Self-inflicted 429 death spiral** — Silent `ensure_session` used to fall through to password + device approval. Worker retries + startup login hammered `get_prompts_status` / login, which share Robinhood’s auth pool. Sustained 429s then triggered a **6-hour** login block.
2. **Approval status polling** — Waiting on `get_prompts_status` after the phone prompt was the hottest endpoint. Polling while already 429’d never saw “approved,” so reauth timed out even when you had already tapped Approve.
3. **Lost `device_token`** — Clearing the pickle without seeding the prior device token made RH treat each attempt as a new device (more prompts / more friction).
4. **No refresh-first path** — Expired access tokens still had a live refresh token, but we jumped straight to password login instead of `grant_type=refresh_token`.
5. **Early abort on empty polls** — A few empty status responses aborted the job while the user was still approving on the phone.
6. **SMS path unhandled** — When RH fell back to SMS instead of (or after) the push prompt, we had no submit-code path.
7. **Placeholder pickle looked refreshable** — Writing `access_token=expired` kept `device_token` but silent refresh treated it as a real session → `invalid_grant` noise and cooldowns.

What finally worked: **skip status poll**, operator **confirm-approval** (and SMS **submit-code** when needed), keep a stable **device_token**, and never auto-password-login from the worker.

## Current defenses

| Layer | Behavior |
| --- | --- |
| Worker / silent `ensure_session` | Pickle restore → OAuth refresh only. Never password + device approval. |
| Placeholder pickle | `__placeholder__` + `placeholder: true` preserves `device_token`; skipped by refresh and startup login. |
| `invalid_grant` | Demote pickle to device-token placeholder; no multi-hour backoff (only 429 gets the long block). |
| Startup | Skips boot login when there is no usable (non-placeholder) pickle. |
| Reauth (dashboard / `POST /robinhood/reauth`) | Password login with device approval; seeds `device_token` from live/stale pickle first. |
| Approval | Default `ROBINHOOD_REAUTH_SKIP_STATUS_POLL=true` — wait for operator confirm instead of polling `get_prompts_status`. |
| SMS / email | `POST /robinhood/reauth/submit-code` while status is `awaiting_approval`. |
| Health / status | `/health` and `/robinhood/reauth/status` expose `prompt_ready`, `code_needed`, `challenge_type`. |
| Alerts | Low-noise `robinhood_logged_out` from the worker; password failures use `robinhood_auth_failed`. |

## Operator runbook (weekly reauth)

1. Be at the PC with the Robinhood app ready (push + SMS).
2. `POST /robinhood/reauth` (or dashboard **Refresh RH auth**).
3. Watch `/robinhood/reauth/status` (or `/health` while `awaiting_approval`):
   - `prompt_ready` → approve on phone, then `POST /robinhood/reauth/confirm-approval`.
   - `code_needed` + `challenge_type` `sms`/`email` → `POST /robinhood/reauth/submit-code` with `{ "code": "..." }`.
4. Success writes a real pickle and clears auth/session alert meta. Do **not** restart during market hours unless needed; a live session already holds tokens in memory.

If you see sustained 429s: stop retrying. Wait for the login block (`robinhood_login_429_backoff_seconds`, default 6h) or clear it only when you know RH has cooled down. Prefer one deliberate reauth with confirm-approval over many automatic attempts.

## MCP note

Robinhood’s official Agentic / MCP auth is a **separate** product surface. It is not a drop-in replacement for this bot’s main-account `robin_stocks` session.
