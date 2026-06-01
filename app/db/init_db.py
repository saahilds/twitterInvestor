from app.config.settings import get_settings
from app.db.base import Base
from app.db.migrations import migrate_trades_table
from app.db.session import SessionLocal, engine
from app.services.recognized_tickers import RecognizedTickerRegistry

# Import models so SQLAlchemy metadata is fully registered.
from app.models import db_models as _db_models  # noqa: F401


def init_db() -> None:
    """Create all DB tables for MVP startup."""
    Base.metadata.create_all(bind=engine)
    migrate_trades_table(engine)
    _seed_recognized_tickers()


def _seed_recognized_tickers() -> None:
    settings = get_settings()
    registry = RecognizedTickerRegistry()
    with SessionLocal() as db:
        registry.seed(db, set(settings.allowed_tickers))
