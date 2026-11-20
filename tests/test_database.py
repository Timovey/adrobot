"""DB helpers: schema → stream_schema migration, session rollback, engine options."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from app import database
from app.database import build_engine, init_db


def test_migrates_legacy_schema_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE campaigns (id INTEGER PRIMARY KEY, keitaro_campaign_id INTEGER UNIQUE,"
                " name VARCHAR(255), alias VARCHAR(255), geo VARCHAR(8), domain_id INTEGER,"
                " group_id INTEGER, traffic_source_id INTEGER, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE streams (id INTEGER PRIMARY KEY, campaign_id INTEGER, keitaro_stream_id INTEGER,"
                ' name VARCHAR(255), stream_type VARCHAR(32), "schema" VARCHAR(32), has_offers BOOLEAN,'
                " is_dirty BOOLEAN, updated_at DATETIME)"
            )
        )
        conn.execute(text("INSERT INTO campaigns (id, keitaro_campaign_id, name) VALUES (1, 77, 'c')"))
        conn.execute(
            text(
                'INSERT INTO streams (id, campaign_id, keitaro_stream_id, name, stream_type, "schema",'
                " has_offers, is_dirty) VALUES (1, 1, 500, 's', 'regular', 'redirect', 0, 0)"
            )
        )

    init_db(engine)
    init_db(engine)  # idempotent

    columns = {c["name"] for c in inspect(engine).get_columns("streams")}
    assert "stream_schema" in columns
    assert "schema" not in columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT stream_schema FROM streams WHERE id = 1")).scalar() == "redirect"
    assert "stream_offers" in inspect(engine).get_table_names()
    engine.dispose()


def test_get_db_rolls_back_on_error(monkeypatch):
    events: list[str] = []

    class FakeSession:
        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    monkeypatch.setattr(database, "SessionLocal", FakeSession)
    gen = database.get_db()
    next(gen)
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))
    assert events == ["rollback", "close"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:///./data/adrobot.db", {"check_same_thread": False}),
        ("postgresql+psycopg://u:p@localhost:5432/adrobot", {}),
    ],
)
def test_sqlite_only_connect_args(monkeypatch, url, expected):
    captured: dict = {}

    def fake_create_engine(u, **kwargs):
        captured.update(kwargs)
        return "engine"

    monkeypatch.setattr(database, "create_engine", fake_create_engine)
    assert build_engine(url) == "engine"
    assert captured["connect_args"] == expected
