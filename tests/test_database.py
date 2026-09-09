"""
Tests app/database.py's _add_missing_columns() -- the lightweight,
no-Alembic stand-in for a real migration, added specifically so
SquishyType.active reaches an already-deployed database (Binit's live
LAN SQLite instance predates this column; create_all() only creates
missing tables, never alters an existing one).
"""
import os
import sys

from sqlalchemy import inspect, text
from sqlmodel import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import _add_missing_columns


def test_adds_active_column_to_a_pre_existing_squishytype_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        # The pre-this-change schema: no `active` column at all, plus a
        # real row, standing in for Binit's already-running LAN database.
        conn.execute(text(
            "CREATE TABLE squishytype ("
            "id INTEGER PRIMARY KEY, name TEXT UNIQUE, "
            "internal_code TEXT UNIQUE, is_giveaway_item BOOLEAN, created_at TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO squishytype (id, name, internal_code, is_giveaway_item, created_at) "
            "VALUES (1, 'Yellow Butter', 'SQ0001', 0, '2026-01-01')"
        ))

    columns_before = {c["name"] for c in inspect(engine).get_columns("squishytype")}
    assert "active" not in columns_before

    _add_missing_columns(target_engine=engine)

    columns_after = {c["name"] for c in inspect(engine).get_columns("squishytype")}
    assert "active" in columns_after

    with engine.connect() as conn:
        row = conn.execute(text("SELECT name, active FROM squishytype WHERE id = 1")).first()
    assert row.name == "Yellow Butter"  # pre-existing data untouched
    assert bool(row.active) is True     # DEFAULT TRUE applied to the existing row


def test_is_a_no_op_when_the_column_already_exists():
    """A fresh database (create_all() already gave it every current
    column) must not error or double-apply anything on the next
    startup -- this runs unconditionally on every init_db() call."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE squishytype (id INTEGER PRIMARY KEY, name TEXT, active BOOLEAN DEFAULT 1)"
        ))

    _add_missing_columns(target_engine=engine)  # should not raise
    _add_missing_columns(target_engine=engine)  # calling it twice must also not raise

    columns = {c["name"] for c in inspect(engine).get_columns("squishytype")}
    assert "active" in columns


def test_is_a_no_op_when_the_table_does_not_exist_yet():
    """A genuinely brand-new database before create_all() has run --
    must not error trying to ALTER a table that isn't there."""
    engine = create_engine("sqlite:///:memory:")
    _add_missing_columns(target_engine=engine)  # should not raise
