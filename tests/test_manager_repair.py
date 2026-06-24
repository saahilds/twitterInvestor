from app.config.account_managers import default_manager_id, legacy_manager_id, parse_bot_managers
from app.config.settings import Settings
from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.db.manager_repair import repair_manager_ids


def test_legacy_manager_id_prefers_robinhood_account() -> None:
    settings = Settings(robinhood_account="joint")
    assert legacy_manager_id(settings) == "joint"


def test_default_manager_id_prefers_robinhood_account() -> None:
    settings = Settings(bot_managers="individual,joint", robinhood_account="joint")
    configs = parse_bot_managers(settings)
    assert default_manager_id(settings, configs) == "joint"


def test_repair_manager_ids_from_account_number(db_session, session_factory) -> None:
    trade = Trade(
        parsed_signal_id=1,
        ticker="NVDA",
        action=SignalAction.BUY,
        amount_usd=1,
        quantity=0.01,
        status="filled",
        simulation=False,
        account_number="acct-joint",
        manager_id="individual",
        response_json="{}",
    )
    db_session.add(trade)
    db_session.commit()

    updated = repair_manager_ids(
        session_factory,
        manager_to_account={"joint": "acct-joint"},
        legacy_manager="joint",
    )

    db_session.refresh(trade)
    assert updated >= 1
    assert trade.manager_id == "joint"


def test_repair_manager_ids_updates_linked_signal(db_session, session_factory) -> None:
    signal = ParsedSignal(
        tweet_pk=1,
        source_tweet_id="t-1",
        ticker="NVDA",
        action=SignalAction.BUY,
        confidence=0.9,
        strength="strong",
        score=1,
        raw_text="buy",
        manager_id="individual",
    )
    db_session.add(signal)
    db_session.flush()
    trade = Trade(
        parsed_signal_id=signal.id,
        ticker="NVDA",
        action=SignalAction.BUY,
        amount_usd=1,
        quantity=0.01,
        status="filled",
        simulation=False,
        account_number="acct-joint",
        manager_id="individual",
        response_json="{}",
    )
    db_session.add(trade)
    db_session.commit()

    repair_manager_ids(
        session_factory,
        manager_to_account={"joint": "acct-joint"},
    )

    db_session.refresh(signal)
    assert signal.manager_id == "joint"
