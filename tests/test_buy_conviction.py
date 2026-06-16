from app.parsing.buy_conviction import BuyConviction, infer_buy_conviction

AAOI_TWEET = """My pick for the full port challenge is...

Just took the position.


$AAOI


Here is the setup.


$AAOI
 is sitting roughly 30% off its all time highs and the reason is dilution overhang. Every time this company raises capital the stock sells off as the market digests new shares."""


def test_reload_conviction_for_starter_add() -> None:
    assert infer_buy_conviction("adding $NVDA starter") == BuyConviction.RELOAD


def test_thesis_conviction_for_aaoi_tweet() -> None:
    assert infer_buy_conviction(AAOI_TWEET) == BuyConviction.THESIS


def test_thesis_conviction_for_weighted_entry() -> None:
    text = "new position for the subs. i just entered $ADEA at a 5% weight. here is the thesis."
    assert infer_buy_conviction(text) == BuyConviction.THESIS


def test_standard_conviction_for_plain_buy() -> None:
    assert infer_buy_conviction("buy $AAOI") == BuyConviction.STANDARD
