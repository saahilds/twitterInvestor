# Plan: multi-leg trade alerts, then stop-loss / options

Status: **plan updated 2026-11-24 — not implemented yet on this branch.**

Priority order:

1. **Multi-leg trade alerts** + **admin action hub** + stronger **trade alerts** (this phase).
2. **Stop-loss** (paper first, live later).
3. **Next types TBD** from low-confidence / misclassified historical tweets.

Out of scope until after (1) and (2): live options, trailing stops, shorts, spreads/rolls.

---

## Decisions locked

| Topic | Decision |
|---|---|
| Correction + execute | Label **and** re-place the corrected leg when within market hours |
| Missing legs | Dashboard **Add missing leg** so you can see / fix / manually trade |
| Execution order | **Sells before buys** in the same tweet (create liquidity for adds) |
| Live vs paper | Keep multi-leg **live** under existing trading switches |
| Retrain | Include classifier retrain **in this phase** |
| Golden case | QCOM full sell (~5%) + GOOGL 4% buy + 1% cash (tweet text below) |

### Labeling storage (best judgment)

**Canonical store: `tweet_labels` (per `tweet_id` + `ticker` + action + segment text).**

Why: it already supports per-ticker ground truth and feeds retrain; `parser_feedback` is tweet-scoped (`unique tweet_id`) and cannot represent mixed multi-leg corrections.

Phase 1 work:

- Dashboard Wrong / Correct buttons become **per leg** and write `tweet_labels` (same shape as review-queue).
- Migrate or dual-write: keep reading old `parser_feedback` for history, but new multi-leg corrections go to `tweet_labels` only.
- Export / retrain uses **one example per leg**.
- Optional later cleanup: stop writing tweet-scoped `parser_feedback` once the UI is fully per-leg.

### Golden tweet (2026-11-24)

```
🚨Trade🚨 Hey guys, Sold the 5% position in $QCOM at $165.30 for an about a 4% loss.
Opened a 4% position in $GOOGL at $277.78 and moved the other 1% to cash.
This gives me a tiny bit of cash and lowers my growth exposure.
Always happy to add Mags, especially in these markets.
```

Expected legs: `SELL $QCOM` (full / ~100% of position), `BUY $GOOGL` (~4% port). Cash remainder is not a trade leg.

---

## Current state (what already exists)

### Parsing / execution

- `segment_trade_units` → one segment per cashtag; hybrid/rules classify each.
- Worker loops all non-`IGNORE` signals and executes each (order today is parse order, **not** yet sells-first).
- Live submit / fail already call `AlertService` (`live_order_submitted`, `trade_failed`) when ntfy is configured — but there is no centralized dashboard place to **act** on those events.

### Gaps

- Tweet feed shows only the **latest** signal badge.
- Tweet-scoped “Wrong: …” cannot correct mixed legs.
- No “add missing leg” when a cashtag never became a `ParsedSignal`.
- No top-of-dashboard **admin hub** for “needs you now” items.
- Phone alerts exist for some events; trade success/fail formatting / priority / deep links into the hub are weak or easy to miss.

---

## Phase 1 scope

### 1A. Show every leg

- Tweet card lists all parsed signals (action, ticker, rejection, trade status).
- Multi-leg badge (`2 legs`, etc.).

### 1B. Per-leg correct + re-execute

- Per-leg Wrong → pick correct action (BUY / SELL / IGNORE / WATCH).
- Writes `tweet_labels` for retrain.
- If market hours and action is BUY/SELL: run the same risk + order path for that leg (live when live trading is on). Outside hours: label only (or queue — see open questions).
- Undo label without automatically undoing a live fill (cancel/revert is separate / manual unless we add it later).

### 1C. Add missing leg

- UI to add ticker + action (+ optional allocation % / sell fraction) when a cashtag was dropped or never parsed.
- Creates a `ParsedSignal` (or equivalent) + label, and offers **Execute now** under the same market-hours rule as 1B.
- Lets you at least see the gap and place manually if you decline execute.

### 1D. Parser hardening + sells-first

- Golden test for the QCOM/GOOGL tweet.
- Segment boundaries for sold/opened, trim/add, swap language.
- Never share one action across mixed local verbs.
- Worker: within one tweet, execute **SELL legs first**, then BUY/WATCH.
- Persist / surface ignored cashtag legs where possible so they appear for labeling.

### 1E. Admin action hub (top of dashboard)

Central strip/panel high on the dashboard for time-sensitive operator work, e.g.:

- Failed live orders (retry / open tweet / mark handled)
- Multi-leg tweets with a failed or missing leg
- Signals `needs_review` / low confidence on trade-header posts
- Pending RH reauth (`prompt_ready` / `code_needed`) — deep link to existing confirm/SMS controls
- Optional: recent live submits awaiting fill confirmation

Each item: short reason, ticker/action, age, primary button (Fix leg / Retry / Confirm RH / Open tweet).

### 1F. Notifications (phone + anywhere Tailscale reaches)

Reuse existing **ntfy** webhook (already how Robinhood / feed alerts reach the phone):

- Ensure **trade submitted** and **trade failed** fire reliably with clear text (ticker, action, $ amount, error).
- Raise priority for **failures** (and optionally multi-leg partial failure).
- Include a short deep-link hint (Tailscale hostname / LAN URL to dashboard admin hub) in the message body so any device on Tailscale can open the hub.
- New event types as needed: `multi_leg_partial_failure`, `admin_action_needed` (deduped).

No new push vendor required unless ntfy delivery is insufficient.

### 1G. Retrain

- After per-leg labels + golden cases land, retrain action classifier and commit the new model in this phase’s PR(s).

### Phase 1 done when

- QCOM/GOOGL-style alerts parse to two correct legs; sells run before buys.
- Dashboard shows all legs; per-leg correct + optional live re-place; add-missing-leg works.
- Admin hub surfaces failures / partial multi-legs / reauth.
- ntfy alerts for live submit + fail are trustworthy and actionable.
- Retrain committed; tests green.

---

## Phase 2 — Stop-loss (after Phase 1)

- `STOP_LOSS` action, paper engine, then gated live.
- Open stop-specific questions deferred until kickoff (fractional shares, “stopped out” without prior stop, no-header stops, full-position default).

---

## Phase 3 — Inventory-driven next types

Cluster `needs_review` / low confidence / IGNORE trade-like / feedback mismatches, then choose options / stop-limit / etc.

---

## Remaining questions

1. **Outside market hours:** If you correct or add a leg after the close, should we **queue it for next open** (auto-place) or **label only** until you hit Execute at open?
2. **Admin hub retention:** How long should items stay — until you dismiss, until fixed, or auto-expire (e.g. 24h)?
3. **Tailscale dashboard URL:** What URL should notifications cite (e.g. `http://<tailscale-name>:8000/`)? If you share it we bake it into alert text via settings.
4. **Alert volume:** Notify on **every** live submit + every fail, or submits only for multi-leg / failures always?
5. **GOOGL vs GOOG:** Robinhood account uses which symbol for Alphabet? (Parser will emit `$GOOGL` from the tweet; we should map if the brokerage symbol differs.)
6. **PR shape:** One PR for Phase 1, or stack (parser+tests → dashboard/hub → alerts → retrain)?
