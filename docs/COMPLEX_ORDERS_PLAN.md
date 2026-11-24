# Plan: multi-leg trade alerts, then stop-loss / options

Status: **plan only — implementation not started on this branch.**

Priority order (locked 2026-11-24):

1. **Multi-leg trade alerts** (same tweet: sell A + buy B, or several buys/sells). Happens most often; already partially parsed/executed, but hard to see and impossible to correct per leg on the dashboard.
2. **Stop-loss** (paper first, live later) — see Phase 2.
3. **Next types TBD** from a review of low-confidence / misclassified historical tweets (options, stop-limit, etc.). Do not invent phases until that inventory is done.

Out of scope until after (1) and (2): live options, trailing stops, shorts, spreads/rolls.

---

## Current state (what already exists)

### Parsing / execution

- `segment_trade_units` splits a tweet into **one segment per cashtag**, preferring action-phrase boundaries (`Adding…`, `Trimming…`, `Sold…`, etc.).
- Hybrid + rules parsers classify **each segment** independently (with a “shared action” helper when one verb governs many tickers, e.g. `Cutting $ASTS and $KRKNF`).
- The worker already loops **all** non-`IGNORE` signals and calls `evaluate_and_execute` per signal.
- Golden / multi-signal tests cover INTC+META buy / ADEA trim style posts.

So multi-leg is not a brand-new order type. The gaps are **visibility, labeling, and robustness** when segments or actions are wrong.

### Dashboard / labeling gaps (the pain)

- Tweet feed shows only the **latest** `signal_action` / `signal_ticker` badge. Sibling legs are invisible in the card.
- `parser_feedback` is **one row per `tweet_id`** with a single `correct_action`. You cannot mark “$QCOM should be SELL and $GOOGL should be BUY”.
- Review-queue labels (`tweet_labels`) *do* support per-ticker actions, but that is a separate UI path from the main tweet “Wrong: …” buttons people actually use.
- Training export from tweet-level feedback cannot represent mixed multi-leg ground truth cleanly.

### Typical failure modes to design against

| Pattern | Example shape | Failure |
|---|---|---|
| Mixed buy + sell in one alert | Sell $QCOM / buy $GOOGL (today) | One badge; wrong shared action; feedback can’t fix both legs |
| Several adds + one trim | INTC + META add, ADEA trim | Works when verbs are clear; dashboard still collapses to one badge |
| Same verb, many tickers | Cutting $A and $B | Shared-action path; OK when both sells |
| Weak / buried verbs | Narrative “rotated out of X into Y” | Segmenter may miss boundary; one leg `IGNORE` |

---

## Phase 1 — Multi-leg trade alerts (priority)

Goal: every cashtag leg in a trade alert is **parsed, shown, executable when actionable, and correctable independently**.

### 1A. Dashboard: show all legs

- Tweet card lists every parsed signal for that tweet (action + ticker + rejection/trade status), not only the latest.
- “Traded” indicator can be per-leg (or a compact summary: `2 traded / 1 ignored`).
- Optional: highlight multi-leg tweets (`N legs`).

### 1B. Per-leg corrections (replace tweet-scoped feedback for multi)

- Feedback / Wrong buttons become **per signal (ticker + action)**.
- Data model options (pick in open questions):
  - Extend `parser_feedback` with optional `ticker` and uniqueness `(tweet_id, ticker)`, **or**
  - Drive training labels only through `tweet_labels` / review-queue and retire single-action tweet feedback for multi-leg posts.
- Export to `labeled_signals.jsonl` must emit **one labeled example per leg** (segment text + action + ticker).
- Correcting a leg must not require inventing a fake single action for the whole tweet.

### 1C. Parser hardening (still BUY/SELL legs — no new `SignalAction`)

- Keep the model as **N separate `BUY`/`SELL`/`WATCH`/`IGNORE` signals** from one tweet. Do **not** add a `MULTI` action unless we later need a tweet-level container for UI only.
- Add golden tests from recent failures (QCOM sell + GOOGL buy, and other multi-leg samples from the DB).
- Tighten segment boundaries for “sold X … bought Y”, “trimmed A … added B”, “from X into Y” / swap language.
- Revisit `_infer_shared_tweet_action`: never force a shared BUY/SELL across segments that already have **mixed** local verbs.
- Persist ignored legs (or at least surface them) so a missed leg is visible and labelable, not silently absent.

### 1D. Execution policy for multi-leg

Already executes each leg independently. Clarify ordering and cash:

- Prefer **sells before buys** within the same tweet when both appear (funds the add; matches “trim A / add B” intent).
- Each leg keeps its own sizing (allocation % / sell fraction) and risk checks.
- One leg failing must not block the others (already mostly true); failures stay visible per leg on the dashboard.

### Phase 1 done when

- Multi-leg tweets show every leg on the dashboard.
- You can mark each leg Wrong → correct action (and undo) without collapsing the tweet to one label.
- Golden tests cover today’s QCOM/GOOGL-style alert and the existing INTC/META/ADEA cases.
- Replay / export produces per-leg labels suitable for retrain.
- No bot restart required for plan-only work; when implementing, prefer off-hours restarts for dashboard/API changes.

---

## Phase 2 — Stop-loss (after multi-leg)

Unchanged intent from the prior plan, deferred:

- New `STOP_LOSS` action; deterministic price/ticker extractors; paper engine vs quotes.
- Live stops only after paper quality bar (separate phase).
- Open questions from the old plan (fractional shares, “stopped out” without a prior stop, no-`Trade`-header stops, full-position quantity) stay open until Phase 2 kickoff.

Details: keep sections below as reference; do not start until Phase 1 is done.

### Stop-loss reference (deferred)

- Paper vs live gated by `STOP_ORDER_EXECUTION_MODE` (`off` / `paper` / `live`).
- Risk: must hold ticker; reject stop ≥ market; reject stop too far below market.
- “Stopped out” posts → outcome records linking to active stops, not a second sell by default (decide at Phase 2).

---

## Phase 3 — Inventory-driven next types

After Phase 1 (and ideally some Phase 2 paper runtime):

1. Pull tweets / signals with `needs_review`, low confidence, `IGNORE` with trade-like text, and dashboard feedback mismatches.
2. Cluster by pattern (options, stop-limit, hedges, commentary false positives, etc.).
3. Only then schedule options paper / stop-limit / other types.

Do not pre-commit to options or stop-limit order until that inventory exists.

---

## Safety notes (all phases)

- Global `SIMULATION_MODE` / `ENABLE_LIVE_TRADING` still override everything.
- Multi-leg Phase 1 does **not** need a new execution-mode env var: legs are ordinary equity BUY/SELL under existing live/sim switches.
- Stop/options modes from the earlier draft still apply when those phases start.
- Never mix paper complex-order P&L into live P&L panels.

---

## Open questions (need answers before / during Phase 1)

1. **Correction scope:** When you mark a leg Wrong on a live multi-leg alert, should that **only** write a training label, or also **re-run / place** that leg with the corrected action?
2. **Feedback storage:** Prefer extending `parser_feedback` to `(tweet_id, ticker)`, or standardize on `tweet_labels` + review-queue for all corrections?
3. **Ignored legs:** If the parser drops a cashtag entirely (no row), should “Add missing leg” exist on the dashboard, or is fixing segmentation + reparse enough?
4. **Execution order:** Confirm **sells before buys** within one tweet as the rule.
5. **Today’s QCOM / GOOGL alert:** Can you paste the exact tweet text (or point to it on the dashboard) so it becomes the first new golden test?
6. **Phase 1 live vs paper:** Multi-leg buys/sells already go live today when enabled. Keep that, or temporarily paper-only multi-leg until labeling UX ships?
7. **Retrain cadence:** After per-leg labels land, should Phase 1 include a classifier retrain commit, or ship UI/parser first and retrain as a follow-up PR?
