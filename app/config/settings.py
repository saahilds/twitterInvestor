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
    poll_interval_seconds: int = 7
    fetch_limit: int = 20
    ignore_replies: bool = True
    ignore_retweets: bool = True

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
    cooldown_seconds: int = 300
    duplicate_window_seconds: int = 300

    simulation_mode: bool = True
    enable_live_trading: bool = False
    broker_backend: Literal["robinhood", "mock"] = "robinhood"
    robinhood_username: str | None = None
    robinhood_password: str | None = None

    log_level: str = "INFO"
    log_file: str = "logs/bot.log"
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 5

    @field_validator("allowed_tickers", mode="before")
    @classmethod
    def parse_allowed_tickers(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [ticker.strip().upper() for ticker in value.split(",") if ticker.strip()]
        if isinstance(value, list):
            return [str(ticker).upper() for ticker in value]
        raise ValueError("ALLOWED_TICKERS must be a comma-separated string or list")

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_poll_interval(cls, value: int) -> int:
        if value < 5:
            return 5
        if value > 10:
            return 10
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
