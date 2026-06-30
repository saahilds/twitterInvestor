from __future__ import annotations

from dataclasses import dataclass

from app.models.db_models import SignalAction


@dataclass(frozen=True, slots=True)
class LabeledExample:
    text: str
    action: SignalAction


# Curated phrases from @CKCapitalxx-style posts. Extend when the bot misses a tweet.
# Use $TICKER placeholders; they are normalized to TICKER before training/inference.
# Retrained from live DB trades/signals on 2026-06-29.
TRAINING_EXAMPLES: tuple[LabeledExample, ...] = (
    # --- BUY (confirmed trade alerts) ---
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
    LabeledExample("added an addition 2% port in $AAOI at $147.86", SignalAction.BUY),
    LabeledExample("added to a position in $AAOI bring my total position to 12%", SignalAction.BUY),
    LabeledExample("took this chance to buy the $ASTS dip filled at $80 even", SignalAction.BUY),
    LabeledExample("got back into $MX today on the dip around $7.50", SignalAction.BUY),
    LabeledExample("insane buy omg $CRWV", SignalAction.BUY),
    LabeledExample("added the $VIVO dip today too thesis has never been better", SignalAction.BUY),
    LabeledExample("i am adding that 3% to $NOK plus an additional margin", SignalAction.BUY),
    LabeledExample(
        "selling all my $MXL was a 5% position. with the funds i am starting a 7% $QCOM position at $209.70",
        SignalAction.BUY,
    ),
    LabeledExample("it just got so cheap i had to take a position myself in $ASTS", SignalAction.BUY),
    # --- SELL (confirmed sell alerts) ---
    LabeledExample("trimmed $META today", SignalAction.SELL),
    LabeledExample("trim $AMD", SignalAction.SELL),
    LabeledExample("sold half my $NVDA", SignalAction.SELL),
    LabeledExample("sell $TSLA", SignalAction.SELL),
    LabeledExample("closed the $AAOI trade", SignalAction.SELL),
    LabeledExample("close $SPY position", SignalAction.SELL),
    LabeledExample("taking profit on $TSLA", SignalAction.SELL),
    LabeledExample("reduce exposure to $QQQ", SignalAction.SELL),
    LabeledExample("reduced $META", SignalAction.SELL),
    LabeledExample("sold $ASTS for the all in", SignalAction.SELL),
    LabeledExample("sold all $ASTS and took the l", SignalAction.SELL),
    LabeledExample("selling the last of my $HOOD for 43% gains at $108.12", SignalAction.SELL),
    LabeledExample(
        "going to sell before end of day and take losses $ASTS will notify when i do end up selling",
        SignalAction.SELL,
    ),
    # --- IGNORE (thesis / commentary / no author trade intent) ---
    LabeledExample(
        "$AAOI is sitting roughly 30% off its all time highs and dilution overhang. "
        "every time this company raises capital the stock sells off as the market digests new shares.",
        SignalAction.IGNORE,
    ),
    LabeledExample("the stock sells off when they raise capital", SignalAction.IGNORE),
    LabeledExample("here is the thesis on $ADEA patents and licensing", SignalAction.IGNORE),
    LabeledExample("$NVDA reports earnings tomorrow", SignalAction.IGNORE),
    LabeledExample("great quarter for $META", SignalAction.IGNORE),
    LabeledExample("$AMD microsoft google use their patents", SignalAction.IGNORE),
    LabeledExample("setup looks interesting on $SPY but no position yet", SignalAction.IGNORE),
    LabeledExample(
        "i most likely will not be making any moves because i'm leveraged already. "
        "but this $AAOI dip is very very tempting",
        SignalAction.IGNORE,
    ),
    LabeledExample(
        "$ASTS down 8% today as people sell the launch news. they are stepping over dollars to pick up pennies.",
        SignalAction.IGNORE,
    ),
    LabeledExample(
        "will possibly look to sell today depending on price $ASTS i think it is close to bottomed out",
        SignalAction.IGNORE,
    ),
    LabeledExample(
        "still holding $ASTS but will sell next week sometime most likely unless something massive happens",
        SignalAction.IGNORE,
    ),
    LabeledExample(
        "for anyone asking about $HLIT and $AMBA. i haven't sold a single share nothing within the thesis has changed",
        SignalAction.IGNORE,
    ),
    LabeledExample("glad i alerted the subs to buy in this morning. this thing is absolutely flying $AAOI", SignalAction.IGNORE),
    LabeledExample(
        "full $MXL thesis for subscribers. as promised here is why i took the position.",
        SignalAction.IGNORE,
    ),
    LabeledExample("me and the sub are now currently up 24% on this position in just a few days $MXL", SignalAction.IGNORE),
    LabeledExample(
        "i will not be adding anything because i am already heavy on margin. but i like $NBIS $ADEA and $PENG here a lot",
        SignalAction.IGNORE,
    ),
    LabeledExample(
        "possibly trade idea soon. $MRLN looking super interesting. not in yet but might look to add today",
        SignalAction.IGNORE,
    ),
    LabeledExample("$PENG hits $60 and is now up almost double from our original buy at $38", SignalAction.IGNORE),
    LabeledExample("up a nice 5% on our dip add $HOOD", SignalAction.IGNORE),
    LabeledExample(
        "$ASTS is an amazing long position this is an all in challenge portfolio so will obviously sell eventually",
        SignalAction.IGNORE,
    ),
    LabeledExample("hmmmmm $ASTS $RKLB", SignalAction.IGNORE),
    LabeledExample("is $PLTR at $116 interesting to you", SignalAction.IGNORE),
    LabeledExample("thank you goat $MU", SignalAction.IGNORE),
    LabeledExample("they're buying the dip $DRAM $EWY", SignalAction.IGNORE),
    LabeledExample("amazing bounce back on the day lots of green here $ASTS up 16%", SignalAction.IGNORE),
    LabeledExample(
        "i covered $KRKNF at $3 and eventually sold at $7 but at $4.40. it is starting to look attractive again.",
        SignalAction.IGNORE,
    ),
    LabeledExample("small swing trade added 2% port in $RDDT for a swing filled at $169.10", SignalAction.BUY),
)
