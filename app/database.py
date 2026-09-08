"""
Database engine setup. SQLite by default (local dev, Binit's LAN
deployment); switches to Postgres automatically when DATABASE_URL is set
-- Render injects this for Josh's hosted deployment. Same
presence-of-env-var pattern as app/storage.py's R2 switch.
"""
import os

from sqlmodel import SQLModel, create_engine, Session

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./squishy_wms.db")

# Render (like Heroku before it) hands out the old "postgres://" scheme;
# SQLAlchemy 2.0 only recognizes "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# pool_pre_ping guards against hosted Postgres closing idle connections
# server-side -- without it, the first query on a dead connection fails
# instead of SQLAlchemy quietly reconnecting first. Harmless no-op check
# for SQLite too, so this applies unconditionally rather than needing a
# dialect check.
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
