from app.config.settings import get_settings
from app.db.base import Base
from app.db.migrations import run_migrations
from app.db.session import SessionLocal, engine
from app.services.recognized_tickers import RecognizedTickerRegistry

# Import models so SQLAlchemy metadata is fully registered.
from app.models import db_models as _db_models  # noqa: F401


def init_db() -> None:
    """Create all DB tables for MVP startup."""
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    _seed_recognized_tickers()


def _seed_recognized_tickers() -> None:
    from app.config.account_managers import parse_bot_managers

    settings = get_settings()
    registry = RecognizedTickerRegistry()
    manager_ids = [cfg.id for cfg in parse_bot_managers(settings)]
    with SessionLocal() as db:
        for manager_id in manager_ids:
            registry.seed(db, set(settings.allowed_tickers), manager_id=manager_id)
