from app.db.base import Base
from app.db.session import engine

# Import models so SQLAlchemy metadata is fully registered.
from app.models import db_models as _db_models  # noqa: F401


def init_db() -> None:
    """Create all DB tables for MVP startup."""
    Base.metadata.create_all(bind=engine)
