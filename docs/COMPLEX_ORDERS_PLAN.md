# Plan: stop-loss, stop-limit, and options alerts

Status: **plan only — nothing here is implemented yet.**

Today the bot only places equity buys and sells. Since PR #6, any tweet that sets a stop loss or mentions an options contract is forced to `IGNORE`, so none of these reach Robinhood. This plan turns those `IGNORE`s into real parsing, first in **paper mode only**, so we can measure accuracy before any of it touches real money.

Order of work:

1. Stop-loss orders on stocks we already hold (paper, then optionally live).
2. Stop-limit orders (parsing and paper only; we have no real examples yet).
3. Buying and selling call/put options (paper only; live options are out of scope for this plan).

---

## 1. Safety model: per-trade-type execution modes

The existing switches stay as they are and keep overriding everything:

- `SIMULATION_MODE=true` or `ENABLE_LIVE_TRADING=false` means **every** order is simulated, including these new types.

New environment variables, one per complex trade type:

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `STOP_ORDER_EXECUTION_MODE` | `off`, `paper`, `live` | `paper` | Stop-loss and stop-limit orders |
| `OPTIONS_EXECUTION_MODE` | `off`, `paper` | `paper` | Option buys and sells |

- `off`: parse and store the signal (so it shows up on the dashboard and in review), but do not simulate or place anything.
- `paper`: parse, store, and run the paper engine (section 5). No Robinhood order calls, ever. Read-only Robinhood calls (holdings, quotes, option market data) are allowed.
- `live`: only for stops, and only in a later phase (section 3, phase 2). The code path refuses `live` unless global live trading is also on.
- Options deliberately have **no `live` value**. If someone sets `OPTIONS_EXECUTION_MODE=live`, settings validation fails at startup with a clear error rather than silently falling back.

Equity buys and sells are unaffected by these variables.

`/health` and the dashboard header show the current mode for each type, so it is obvious at a glance that stops and options are paper-only.

---

## 2. What the tweets actually look like

Counted from the 990 stored tweets (Aug 25 – Nov 18, 2026).

### Stop losses: 16 tweets, 3 with a `Trade` header

| Kind | Example | Count (approx.) |
|---|---|---|
| Set a stop | "Trade — Set a stop loss at $205 on $COHR." / "$COHR stop loss set at $224.50" / "Setting a stop loss on $BE at $222." / "Placing a stop loss here at $108 for $MOD." | 5 |
| Move a stop | "Moving up my stop loss on $HUT to $81 which is in the profit." / "Moving stop loss up to $84 for $HUT." | 3 |
| Stop was hit (outcome) | "Got stopped out here on $BE locking in about 15% gains." / "Got stopped out." / "$HUT stop loss just got hit." | 6 |
| Plan or commentary | "Will share some stop losses ... shortly" / "For $APP will set SL at $299" / "Nearing our stop loss on $HUT." | 3 |

Observations that shape the design:

- The trader often sets stops **without** a `Trade` header (MOD, HUT). The header is not a reliable signal for stops.
- Stops are always on a stock already held, always with an explicit dollar price.
- "Stopped out" posts sometimes have no ticker at all ("Got stopped out.") and arrive minutes after the stop was set.
- One example is a stop on a short ("For $CRWD will cover if we break above $268"). The bot does not short, so buy-side stops are out of scope.

### Stop-limit: 0 tweets. Trailing stop: 0 tweets.

We have no real examples. Stop-limit support would be built against synthetic test phrases only, and kept paper-only until real examples show up.

### Options: 28 tweets mention options, 3 with a `Trade` header

Actionable ones:

- Buy: "Trade — Added some $NVDA $150 Puts expiring on Friday for my hedge. In with about 1% port ... In at ~$3.30."
- Sell: "Trade — Sold my $SMH puts for about 85% gains here."
- Sell: "Trade — Closed my $NVDA puts here for 100% gain."

Everything else is commentary ("Puts are up 50% now", "might roll them over", other people's flow, "Who's buying leaps here?"). The intent language from the label batch (`looking for my entry`, `will alert when I add`, `may sell and roll`) must stay `IGNORE`.

Note: "2x short" trades such as `$NVD` and `$DAMD` are leveraged inverse ETFs, which are ordinary equities. They already go through the normal buy path and are not part of this plan.

---

## 3. Phases

### Phase 1 — Stop-loss parsing + paper engine

**Parsing**

- Add a new signal action `STOP_LOSS` (alongside `BUY`, `SELL`, `WATCH`, `IGNORE`). It covers both setting and moving a stop, since both mean "the protective stop is now at $X".
- Add fields to `TradeSignal` and `ParsedSignal`: `stop_price`, `limit_price` (unused until phase 3), `order_kind` (`market`, `limit`, `stop`, `stop_limit`), and `instrument` (`equity`, `option`).
- Replace the current `is_stop_loss_setup → IGNORE` gate with a deterministic extractor: ticker + stop price from phrases like "stop loss at $X on $T", "$T stop loss set at $X", "moving (up) stop (loss) on $T to $X". Rules first; the ML classifier only learns to tell `STOP_LOSS` from `SELL`/`IGNORE`.
- Plan and commentary phrases ("will share", "will set", "nearing our stop") stay `IGNORE`.
- "Stopped out" posts become a separate outcome record (not a trade signal). They resolve to the most recent active stop for the named ticker, or the most recent active stop overall when no ticker is given.
- Relabel the three stop-loss examples currently labeled `IGNORE` in `data/labeled_signals.jsonl` to `STOP_LOSS`, add the non-header MOD and HUT examples, and retrain.

**Risk checks (apply in every mode)**

- Reject when we do not hold the ticker (`no_position_for_stop`).
- Reject when the stop price is at or above the current price (`stop_above_market`); it would fill immediately, which is a sell and should be parsed as one.
- Reject stops more than 25% below the current price as a likely misparse (`stop_too_far`). Threshold is configurable.
- Quantity defaults to the whole current position.

**Paper engine** (section 5) records a paper stop and checks it against quotes each worker cycle.

**Done when:** all real stop examples above parse to the right action, ticker, and price; paper stops show on the dashboard; a "stopped out" tweet links to the matching paper stop; full test suite passes.

### Phase 2 — Live stop-loss orders (gated, later)

Only after phase 1 has run for a while and the paper log shows correct parses. Turned on with `STOP_ORDER_EXECUTION_MODE=live`.

- Place with `robin_stocks` `order_sell_stop_loss(symbol, quantity, stopPrice, timeInForce="gtc")`.
- Moving a stop means cancel the existing open stop order for that ticker, then place the new one. If the cancel fails, do not place the new one, and alert.
- Before any equity `SELL` on a ticker that has an open stop, cancel the stop first. Otherwise Robinhood may reject the sell because shares are held by the open order.
- When a live stop fills, the existing trade-status sync records it as a filled sell; a later "stopped out" tweet must not trigger a second sell.
- Store `broker_order_id` and stop state on a new `stop_orders` table (ticker, stop price, quantity, status, mode, source tweet, replaced-by).

### Phase 3 — Stop-limit (parsing + paper only)

- Parse "stop limit at $X, limit $Y" style phrases into `order_kind=stop_limit` with both prices. Validate limit ≤ stop for sells.
- Paper engine: triggers at the stop price, fills only if the quote stays at or above the limit price; otherwise records "triggered, not filled".
- Synthetic tests only until real examples appear. No live mode in this plan.

### Phase 4 — Options parsing + paper engine

**Parsing** — when the options gate fires, extract a contract instead of returning `IGNORE`:

- Underlying ticker, `put` or `call`, strike ("$150 Puts"), expiration, entry price ("In at ~$3.30"), and sizing ("about 1% port").
- Expiration is resolved relative to the tweet time: "expiring on Friday" means the next Friday on or after the tweet date; explicit dates like "11/21" or "Dec 19" are parsed directly.
- Action stays `BUY` or `SELL` (this matches how the label batch was tagged), with `instrument=option`.
- Sells ("Sold my $SMH puts", "Closed my $NVDA puts") do not need a strike. They match the open paper position on that underlying and type.
- Any missing required field (type, strike, or expiration on a buy) marks the signal `needs_review` and records it without a paper fill.
- Hedging and roll language ("might roll", "may sell and buy cheaper insurance") stays `IGNORE`.

**Paper engine** — opens a paper option position at the option's market mid price at alert time (read-only `get_option_market_data`), sized as a % of the sleeve like equity buys, in whole contracts. Closes on a matching sell alert, or at expiration (intrinsic value). A sell with no open paper position is recorded as "unmatched close" for learning.

**Done when:** the three real `Trade` examples parse correctly, commentary examples stay `IGNORE`, paper positions and P&L show on the dashboard.

### Out of scope

- Live options trading. That needs its own plan: limit orders only, per-trade and daily contract caps, spread/liquidity checks, and a long paper record first.
- Trailing stops, buy-side stops, shorts, option spreads, and rolls.

---

## 4. Data model changes

- `SignalAction`: add `STOP_LOSS`.
- `parsed_signals`: add `order_kind`, `instrument`, `stop_price`, `limit_price`, `option_type`, `option_strike`, `option_expiration`, `execution_mode` (`live`, `paper`, `off`).
- `trades`: add `instrument`, `execution_mode`, and the option contract fields. `simulation` stays as-is for the equity path.
- New `stop_orders` table (phase 1 for paper, reused in phase 2 for live).
- New `paper_option_positions` table (phase 4).
- Migrations go through `app/db/migrations.py` like existing columns.

Paper results never mix into live P&L. The dashboard gets a separate "Paper: complex orders" panel, and the daily digest gets a short section listing new paper stops, triggers, and option positions.

---

## 5. Paper engine

Runs inside the existing worker maintenance step, so no new process.

- Stops: each cycle, during market hours, fetch quotes for tickers with active paper stops (existing `QuoteProvider`). If the last price is at or below the stop, mark it triggered at that price and record hypothetical proceeds versus selling at the time the stop was set.
- Stop-limit: same trigger; fill only if the price is at or above the limit.
- Options: mark open positions to market on the dashboard refresh interval; settle at expiration.
- Every paper event is written to the DB and shown in the review queue, so wrong parses can be corrected and fed back into `labeled_signals.jsonl`.

---

## 6. Testing

- Unit tests for each extractor using the real tweets quoted above, plus synthetic stop-limit phrases.
- Settings tests: `OPTIONS_EXECUTION_MODE=live` fails validation; `STOP_ORDER_EXECUTION_MODE=live` with global simulation on still simulates.
- A broker test that asserts no Robinhood order function is called in `paper` mode (patch `robin_stocks` order functions to raise).
- Replay test: run the stored Aug–Nov tweets through the parser and compare against labels, as the existing replay scripts do.

---

## 7. Open questions

1. **Fractional shares and stops.** Most bot positions are fractional because buys are sized in dollars. Robinhood may not accept stop orders on fractional quantities. If not, live stops would cover only the whole-share part. Is that acceptable, or should live stops wait until we confirm fractional support?
2. **"Stopped out" when we had no stop.** If the trader posts "Got stopped out on $BE" and we never placed a matching stop, should that become a market `SELL` of our position? Proposal: yes in paper mode only during phase 1, and decide for live after seeing results.
3. **Stops without a `Trade` header.** The trader sets stops without the header fairly often. Proposal: accept them when the phrase has an explicit stop price and we hold the ticker, since that combination is hard to misread.
4. **Default quantity.** Proposal: a stop always covers the full position. Any reason to match only the trader's share of the position?
5. **How long to paper trade before allowing live stops.** Proposal: at least 10 real stop alerts parsed correctly with no wrong-ticker or wrong-price parses.
