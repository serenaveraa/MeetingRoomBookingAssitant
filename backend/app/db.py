from collections.abc import Generator

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.models import Base, ODC_COMMON_ROOM_NAME, Room

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            # Keep a single shared in-memory DB across connections (tests).
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(
            bind=_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def seed_odc_room(session: Session) -> Room:
    room = session.scalar(select(Room).where(Room.name == ODC_COMMON_ROOM_NAME))
    if room is None:
        room = Room(name=ODC_COMMON_ROOM_NAME)
        session.add(room)
        session.commit()
        session.refresh(room)
    return room


def init_db() -> None:
    """Create tables and seed the single ODC common room."""
    engine = get_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        _ensure_postgres_booking_exclusion(engine)
    with get_session_factory()() as session:
        seed_odc_room(session)


def _ensure_postgres_booking_exclusion(engine: Engine) -> None:
    """Install the production overlap guarantee after tables exist."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'ex_bookings_confirmed_room_time'
                    ) THEN
                        ALTER TABLE bookings
                        ADD CONSTRAINT ex_bookings_confirmed_room_time
                        EXCLUDE USING gist (
                            room_id WITH =,
                            tstzrange(start_at, end_at, '[)') WITH &&
                        )
                        WHERE (status = 'confirmed');
                    END IF;
                END $$;
                """
            )
        )


def reset_engine() -> None:
    """Clear cached engine (useful in tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
