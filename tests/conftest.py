"""Shared pytest fixtures for Adrobot tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Database isolation. This MUST happen before any `app.*` import: app.database
# builds its module-level engine from get_settings() at import time, and the
# FastAPI lifespan (TestClient used as a context manager) calls init_db() on it.
# A process env var beats `.env` in pydantic-settings, so the real data/adrobot.db
# (or a Postgres DATABASE_URL from .env) is never opened by the test run.
# ---------------------------------------------------------------------------
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="adrobot-pytest-"))
_TEST_DB_FILE = (_TEST_DB_DIR / "adrobot-test.db").as_posix()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_FILE}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.database import Base, engine as _app_engine, get_db  # noqa: E402
from app.main import app, get_client  # noqa: E402
from app.services.keitaro import KeitaroClient, KeitaroError  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_app_database() -> Generator[None, None, None]:
    """Fail loudly if the app engine is not the throwaway test DB; clean it up afterwards."""
    url = _app_engine.url
    assert url.get_backend_name() == "sqlite" and url.database == _TEST_DB_FILE, (
        f"app engine points at {url!s}, not the isolated test DB; "
        "refusing to run tests against a real database"
    )
    yield


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop the throwaway DB even if the isolation guard aborted the run."""
    _app_engine.dispose()
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit / mocked API fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Isolated in-memory SQLite session for each test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import models so metadata is populated
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


class FakeKeitaro:
    """In-memory Keitaro Admin API stand-in for fast CI tests."""

    def __init__(self) -> None:
        self._next_id = 1000
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {}
        self.offers: dict[int, dict] = {
            101: {"id": 101, "name": "Offer Alpha", "state": "active", "group_id": 1},
            102: {"id": 102, "name": "Offer Beta", "state": "active", "group_id": 1},
            103: {"id": 103, "name": "Offer Gamma", "state": "active", "group_id": 1},
        }
        self.domains = [{"id": 1813, "name": "kylovoa.com", "state": "active"}]
        self.groups = [{"id": 10332, "name": "FORTESTS", "type": "campaigns"}]
        self.sources = [{"id": 836, "name": "FORTESTS", "state": "active"}]
        self.calls: Counter[str] = Counter()
        self._failures: dict[str, tuple[int, Exception]] = {}
        self.update_stream_bodies: list[dict] = []

    def fail(self, method: str, exc: Exception, on_call: int = 1) -> None:
        """Make the `on_call`-th call (1-based, counted from now) of `method` raise `exc`."""
        self._failures[method] = (self.calls[method] + on_call, exc)

    def _track(self, method: str) -> None:
        self.calls[method] += 1
        planned = self._failures.get(method)
        if planned and planned[0] == self.calls[method]:
            del self._failures[method]
            raise planned[1]

    def _alloc(self) -> int:
        self._next_id += 1
        return self._next_id

    def list_campaigns(self, limit: int | None = None, offset: int | None = None) -> list[dict]:
        items = list(self.campaigns.values())
        if offset:
            items = items[offset:]
        if limit is not None:
            items = items[:limit]
        return items

    def find_campaign_by_alias(self, alias: str) -> dict | None:
        return next((dict(c) for c in self.campaigns.values() if c.get("alias") == alias), None)

    def get_campaign(self, campaign_id: int) -> dict:
        if campaign_id not in self.campaigns:
            raise KeitaroError(f"Campaign {campaign_id} not found", status_code=404)
        return dict(self.campaigns[campaign_id])

    def create_campaign(self, body: dict) -> dict:
        self._track("create_campaign")
        cid = self._alloc()
        row = {"id": cid, **body}
        self.campaigns[cid] = row
        return dict(row)

    def update_campaign(self, campaign_id: int, body: dict) -> dict:
        if campaign_id not in self.campaigns:
            raise KeitaroError(f"Campaign {campaign_id} not found", status_code=404)
        self.campaigns[campaign_id].update(body)
        return dict(self.campaigns[campaign_id])

    def delete_campaign(self, campaign_id: int) -> None:
        self._track("delete_campaign")
        if campaign_id not in self.campaigns:
            raise KeitaroError(f"Campaign {campaign_id} not found", status_code=404)
        self.campaigns[campaign_id]["state"] = "archived"

    def disable_campaign(self, campaign_id: int) -> None:
        self._track("disable_campaign")
        if campaign_id not in self.campaigns:
            raise KeitaroError(f"Campaign {campaign_id} not found", status_code=404)
        self.campaigns[campaign_id]["state"] = "disabled"

    def list_campaign_streams(self, campaign_id: int) -> list[dict]:
        return [dict(s) for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    def create_stream(self, body: dict) -> dict:
        self._track("create_stream")
        sid = self._alloc()
        row = {"id": sid, **body}
        # Normalize offers list
        row["offers"] = list(body.get("offers") or [])
        self.streams[sid] = row
        return dict(row)

    def update_stream(self, stream_id: int, body: dict) -> dict:
        self._track("update_stream")
        if stream_id not in self.streams:
            raise KeitaroError(f"Stream {stream_id} not found", status_code=404)
        self.update_stream_bodies.append(body)
        self.streams[stream_id].update(body)
        if "offers" in body:
            self.streams[stream_id]["offers"] = list(body["offers"])
        return dict(self.streams[stream_id])

    def delete_stream(self, stream_id: int) -> None:
        self._track("delete_stream")
        if stream_id not in self.streams:
            raise KeitaroError(f"Stream {stream_id} not found", status_code=404)
        del self.streams[stream_id]

    def get_stream(self, stream_id: int) -> dict:
        if stream_id not in self.streams:
            raise KeitaroError(f"Stream {stream_id} not found", status_code=404)
        return dict(self.streams[stream_id])

    def list_offers(self) -> list[dict]:
        return [dict(o) for o in self.offers.values()]

    def get_offer(self, offer_id: int) -> dict:
        if offer_id not in self.offers:
            raise KeitaroError(f"Offer {offer_id} not found", status_code=404)
        return dict(self.offers[offer_id])

    def list_domains(self) -> list[dict]:
        return list(self.domains)

    def list_groups(self, group_type: str = "campaigns") -> list[dict]:
        return list(self.groups)

    def list_traffic_sources(self) -> list[dict]:
        return list(self.sources)


@pytest.fixture()
def fake_keitaro() -> FakeKeitaro:
    return FakeKeitaro()


@pytest.fixture()
def test_settings() -> Settings:
    """Deterministic settings (ignores .env); mutate in a test to enable auth etc."""
    return Settings(
        _env_file=None,
        keitaro_api_key="test-key",
        campaign_name_prefix="test_timovey_",
        google_redirect_url="https://www.google.com/",
        adrobot_api_token="",
        default_domain_id=None,
        default_group_id=None,
        default_traffic_source_id=None,
    )


@pytest.fixture()
def api_client(
    db_session: Session, fake_keitaro: FakeKeitaro, test_settings: Settings
) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with mocked Keitaro + isolated DB."""

    def _override_db() -> Generator[Session, None, None]:
        yield db_session

    def _override_client() -> FakeKeitaro:
        return fake_keitaro

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_client] = _override_client
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Live Keitaro fixtures
# ---------------------------------------------------------------------------


def _live_enabled() -> bool:
    return os.environ.get("RUN_LIVE_TESTS", "").strip() in {"1", "true", "True", "yes", "YES"}


# Keitaro campaign IDs created by live tests (printed in the terminal summary).
LIVE_CREATED_CAMPAIGNS: list[tuple[int, str]] = []


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if LIVE_CREATED_CAMPAIGNS:
        terminalreporter.section("live Keitaro campaigns created (cleanup state)")
        for campaign_id, state in LIVE_CREATED_CAMPAIGNS:
            terminalreporter.write_line(f"campaign {campaign_id}: {state}")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: hits the real Keitaro Admin API (requires .env; set RUN_LIVE_TESTS=1)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _live_enabled():
        return
    skip_live = pytest.mark.skip(reason="Live Keitaro tests require RUN_LIVE_TESTS=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(scope="session")
def live_settings():
    """Load real settings from .env for live tests."""
    from app.config import Settings

    # Bypass lru_cache so tests always read current .env
    settings = Settings()
    if not settings.keitaro_api_key:
        pytest.skip("KEITARO_API_KEY not set in .env")
    return settings


@pytest.fixture(scope="session")
def live_client(live_settings) -> Generator[KeitaroClient, None, None]:
    client = KeitaroClient(live_settings)
    yield client
    client.close()


@pytest.fixture(scope="session")
def live_offer_ids(live_client: KeitaroClient) -> list[int]:
    """Discover real active offer IDs from Keitaro (multi-offer tests skip themselves if < 2)."""
    offers = live_client.list_offers()
    active = [
        int(o["id"])
        for o in offers
        if o.get("state") != "deleted" and o.get("id") is not None
    ]
    if not active:
        pytest.skip("Need at least 1 active offer in Keitaro for live tests")
    return active[:5]


@pytest.fixture()
def live_db_session() -> Generator[Session, None, None]:
    """Separate on-disk temp DB for live API TestClient tests."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def live_api_client(
    live_db_session: Session, live_client: KeitaroClient, live_settings
) -> Generator[TestClient, None, None]:
    """TestClient wired to real Keitaro + isolated local DB."""

    def _override_db() -> Generator[Session, None, None]:
        yield live_db_session

    def _override_client() -> KeitaroClient:
        return live_client

    def _override_settings():
        return live_settings

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_client] = _override_client
    app.dependency_overrides[get_settings] = _override_settings
    headers = (
        {"Authorization": f"Bearer {live_settings.adrobot_api_token}"}
        if live_settings.adrobot_api_token
        else {}
    )
    with TestClient(app, headers=headers) as client:
        yield client
    app.dependency_overrides.clear()
