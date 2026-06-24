from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "twitter-trade-bot"
    database_url: str = "sqlite:///./trading_bot.db"

    target_account: str = "CKCapitalxx"
    poll_interval_seconds: int = 60
    dashboard_positions_refresh_seconds: int = 300
    fetch_limit: int = 20
    ignore_replies: bool = True
    ignore_retweets: bool = True
    twitter_backend: Literal["playwright", "mock"] = "playwright"
    playwright_headless: bool = False
    playwright_timeout_ms: int = 45_000
    playwright_profile_load_retries: int = 3
    playwright_user_data_dir: str = ".playwright/x-profile"
    playwright_channel: str = "chrome"
    playwright_cdp_url: str | None = None
    playwright_require_login: bool = True
    playwright_login_timeout_seconds: int = 300
    backfill_max_scrolls: int = 150
    backfill_scroll_pause_ms: int = 1500

    allowed_tickers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "AAPL",
            "MSFT",
            "NVDA",
            "TSLA",
            "META",
            "AMZN",
            "GOOGL",
            "AMD",
            "QQQ",
            "SPY",
        ]
    )
    default_trade_size_usd: float = 1.0
    max_trade_size_usd: float = 5.0
    new_ticker_size_multiplier: float = 10.0
    thesis_trade_min_usd: float = 500.0
    thesis_trade_max_usd: float = 1000.0
    cash_buffer_usd: float = 0.0
    min_buy_notional_usd: float = 1.0
    cooldown_seconds: int = 300
    duplicate_window_seconds: int = 300

    signal_parser_backend: Literal["keywords", "hybrid"] = "hybrid"
    signal_ml_min_confidence: float = 0.42
    signal_ml_min_margin: float = 0.08
    # 0 = disabled. When > 0, BUY for tickers outside ALLOWED_TICKERS / recognized_tickers
    # requires parser confidence at least this high (keyword + hybrid signals set confidence).
    min_buy_confidence_unlisted: float = 0.0
    default_sell_fraction: float = 1.0
    min_sell_notional_usd: float = 1.0

    chart_ytd_baseline_usd: float = 5000.0

    simulation_mode: bool = True
    enable_live_trading: bool = False
    broker_backend: Literal["robinhood", "mock"] = "robinhood"
    order_execution_mode: Literal["limit_at_ask", "fractional_market"] = "limit_at_ask"
    trading_window_enabled: bool = True
    us_symbols_only: bool = True
    max_trades_per_ticker_per_day: int = 1
    daily_limit_counts_simulation: bool = False
    robinhood_username: str | None = None
    robinhood_password: str | None = None
    robinhood_mfa_secret: str | None = None
    robinhood_account: str | None = None
    bot_managers: str | None = None
    bot_managers_enable_all: bool = False
    robinhood_login_retry_seconds: int = 300
    robinhood_login_429_backoff_seconds: int = 900
    robinhood_session_validate_seconds: int = 120

    log_level: str = "INFO"
    log_file: str = "logs/bot.log"
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 5

    pnl_include_simulation: bool = True
    pnl_quote_cache_seconds: int = 60

    @field_validator("allowed_tickers", mode="before")
    @classmethod
    def parse_allowed_tickers(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [ticker.strip().upper() for ticker in value.split(",") if ticker.strip()]
        if isinstance(value, list):
            return [str(ticker).upper() for ticker in value]
        raise ValueError("ALLOWED_TICKERS must be a comma-separated string or list")

    @field_validator("playwright_cdp_url", mode="before")
    @classmethod
    def normalize_playwright_cdp_url(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_poll_interval(cls, value: int) -> int:
        if value < 60:
            return 60
        if value > 3600:
            return 3600
        return value

    @field_validator("max_trade_size_usd")
    @classmethod
    def validate_max_trade_size(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("MAX_TRADE_SIZE_USD must be > 0")
        return value

    @property
    def live_trading_enabled(self) -> bool:
        """Live trading is only enabled with explicit flag and simulation off."""
        return self.enable_live_trading and not self.simulation_mode


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for process lifetime."""
    return Settings()
