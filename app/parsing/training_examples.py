from __future__ import annotations

from dataclasses import dataclass

from app.models.db_models import SignalAction


@dataclass(frozen=True, slots=True)
class LabeledExample:
    text: str
    action: SignalAction


# Curated phrases from @CKCapitalxx-style posts. Extend when the bot misses a tweet.
# Use $TICKER placeholders; they are normalized to TICKER before training/inference.
TRAINING_EXAMPLES: tuple[LabeledExample, ...] = (
    # --- BUY ---
    LabeledExample("adding $NVDA starter", SignalAction.BUY),
    LabeledExample("adding nvda starter", SignalAction.BUY),
    LabeledExample("just took the position in $AAOI", SignalAction.BUY),
    LabeledExample("took a position in $TSLA", SignalAction.BUY),
    LabeledExample("took position on $META", SignalAction.BUY),
    LabeledExample("new position for the subs. i just entered $ADEA at a 5% weight", SignalAction.BUY),
    LabeledExample("i just entered $ADEA at a 5% weight. here is the thesis", SignalAction.BUY),
    LabeledExample("entered $SPY at a small weight", SignalAction.BUY),
    LabeledExample("opened a new position in $AMD", SignalAction.BUY),
    LabeledExample("scaling in to $QQQ", SignalAction.BUY),
    LabeledExample("scale in on $NVDA", SignalAction.BUY),
    LabeledExample("bought more $AAPL", SignalAction.BUY),
    LabeledExample("buy $TSLA here", SignalAction.BUY),
    LabeledExample("going long $META", SignalAction.BUY),
    LabeledExample("starter in $NVDA", SignalAction.BUY),
    LabeledExample("adding to $AMD", SignalAction.BUY),
    LabeledExample(
        "new position for the subs. i just entered $ADEA at a 5% weight. here is the thesis. "
        "$ADEA owns valuable tech patents and charges the biggest companies on earth to use them.",
        SignalAction.BUY,
    ),
    # --- SELL ---
    LabeledExample("trimmed $META today", SignalAction.SELL),
    LabeledExample("trim $AMD", SignalAction.SELL),
    LabeledExample("sold half my $NVDA", SignalAction.SELL),
    LabeledExample("sell $TSLA", SignalAction.SELL),
    LabeledExample("closed the $AAOI trade", SignalAction.SELL),
    LabeledExample("close $SPY position", SignalAction.SELL),
    LabeledExample("taking profit on $TSLA", SignalAction.SELL),
    LabeledExample("reduce exposure to $QQQ", SignalAction.SELL),
    LabeledExample("reduced $META", SignalAction.SELL),
    # --- IGNORE (thesis / commentary, no author trade intent) ---
    LabeledExample(
        "$AAOI is sitting roughly 30% off its all time highs and dilution overhang. "
        "every time this company raises capital the stock sells off as the market digests new shares.",
        SignalAction.IGNORE,
    ),
    LabeledExample("the stock sells off when they raise capital", SignalAction.IGNORE),
    LabeledExample("here is the thesis on $ADEA patents and licensing", SignalAction.IGNORE),
    LabeledExample("$NVDA reports earnings tomorrow", SignalAction.IGNORE),
    LabeledExample("watching $TSLA for a breakout", SignalAction.IGNORE),
    LabeledExample("great quarter for $META", SignalAction.IGNORE),
    LabeledExample("$AMD microsoft google use their patents", SignalAction.IGNORE),
    LabeledExample("setup looks interesting on $SPY but no position yet", SignalAction.IGNORE),
)
