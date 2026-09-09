"""
Database engine setup. SQLite by default (local dev, Binit's LAN
deployment); switches to Postgres automatically when DATABASE_URL is set
-- Render injects this for Josh's hosted deployment. Same
presence-of-env-var pattern as app/storage.py's R2 switch.
"""
import os

from sqlalchemy import inspect, text
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
    _add_missing_columns()


# create_all() only creates missing *tables* -- it never alters an
# existing one, so a genuinely new column on a table that predates it
# (like SquishyType.active) needs handling here instead. This project
# deliberately has no migration framework (Alembic was ruled out during
# the original cloud-hosting design), so this stays a plain, portable
# ALTER TABLE rather than introducing one just for a single column.
# Runs on every startup; a no-op everywhere the column already exists,
# which includes every brand-new database (create_all() gives those the
# column from the current model definition on first create).
_COLUMN_ADDITIONS = {
    "squishytype": [("active", "BOOLEAN DEFAULT TRUE")],
}


def _add_missing_columns(target_engine=None):
    target_engine = target_engine or engine
    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())
    with target_engine.begin() as conn:
        for table_name, columns in _COLUMN_ADDITIONS.items():
            if table_name not in existing_tables:
                continue  # nothing to alter -- create_all() will have made it with every current column
            existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
            for column_name, column_def in columns:
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))


def get_session():
    with Session(engine) as session:
        yield session
