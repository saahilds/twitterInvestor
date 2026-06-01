import logging

import pytest

from app.config.settings import Settings
from app.execution.robinhood_broker import RobinhoodBroker


@pytest.mark.asyncio
async def test_verify_login_missing_credentials() -> None:
    settings = Settings(
        robinhood_username=None,
        robinhood_password=None,
    )
    broker = RobinhoodBroker(settings=settings, logger=logging.getLogger("test"))
    result = await broker.verify_login()
    assert result["ok"] is False
    assert result["error"] == "missing_robinhood_credentials"
