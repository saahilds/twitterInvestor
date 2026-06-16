from __future__ import annotations

import logging
import time
from typing import Callable

from app.execution.robinhood_session import RobinhoodSessionManager

try:
    from robin_stocks import robinhood as rh
except Exception:  # pragma: no cover
    rh = None


class QuoteProvider:
    """Fetch latest prices (slightly delayed) for P&L marks."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        session_manager: RobinhoodSessionManager | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self._session = session_manager or RobinhoodSessionManager(settings=settings, logger=logger)
        self._cache: dict[str, tuple[float, float]] = {}

    def get_prices(self, tickers: list[str], *, cache_seconds: int = 60) -> dict[str, float | None]:
        symbols = sorted({symbol.upper() for symbol in tickers if symbol})
        now = time.monotonic()
        prices: dict[str, float | None] = {}
        missing: list[str] = []

        for symbol in symbols:
            cached = self._cache.get(symbol)
            if cached and now - cached[1] < cache_seconds:
                prices[symbol] = cached[0]
            else:
                missing.append(symbol)

        if not missing:
            return prices

        fetched = self._fetch_prices(missing)
        for symbol in missing:
            price = fetched.get(symbol)
            if price is not None:
                self._cache[symbol] = (price, now)
            prices[symbol] = price
        return prices

    def _fetch_prices(self, tickers: list[str]) -> dict[str, float | None]:
        if rh is None:
            self.logger.warning("quote_provider_robin_stocks_unavailable")
            return {ticker: None for ticker in tickers}

        if not self._ensure_login():
            return {ticker: None for ticker in tickers}

        result: dict[str, float | None] = {}
        for ticker in tickers:
            result[ticker] = self._fetch_single(ticker)
        return result

    def _ensure_login(self) -> bool:
        return self._session.ensure_session() is None

    @staticmethod
    def _fetch_single(ticker: str) -> float | None:
        if rh is None:
            return None
        try:
            latest = rh.stocks.get_latest_price(ticker, includeExtendedHours=True)
            if isinstance(latest, list) and latest:
                return float(latest[0])
            if isinstance(latest, str):
                return float(latest)
        except Exception:
            return None

        quote = rh.stocks.get_stock_quote_by_symbol(ticker)
        if isinstance(quote, list) and quote:
            quote = quote[0]
        if isinstance(quote, dict):
            for key in ("last_trade_price", "last_extended_hours_trade_price", "ask_price"):
                raw = quote.get(key)
                if raw is not None:
                    return float(raw)
        return None


def quote_provider_factory(settings: Settings, logger: logging.Logger) -> Callable[[], QuoteProvider]:
    def _factory() -> QuoteProvider:
        return QuoteProvider(settings=settings, logger=logger)

    return _factory
