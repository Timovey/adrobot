"""create_campaign: validate → remote → DB, with best-effort remote rollback on failure."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import CampaignLocal
from app.services.keitaro import KeitaroError
from tests.conftest import FakeKeitaro

PAYLOAD = {
    "name": "pytest_comp",
    "geo": "AU",
    "offer_id": 101,
    "domain_id": 1813,
    "group_id": 10332,
    "traffic_source_id": 836,
}


def _only_campaign(fake: FakeKeitaro) -> dict:
    assert len(fake.campaigns) == 1
    return next(iter(fake.campaigns.values()))


def test_second_stream_failure_archives_campaign_and_deletes_first_stream(
    api_client: TestClient, fake_keitaro: FakeKeitaro, db_session: Session
):
    fake_keitaro.fail("create_stream", KeitaroError("stream boom", status_code=502), on_call=2)

    r = api_client.post("/api/campaigns", json=PAYLOAD)
    assert r.status_code == 502
    assert "stream boom" in r.json()["detail"]

    campaign = _only_campaign(fake_keitaro)
    assert campaign["state"] == "archived"
    assert fake_keitaro.streams == {}
    assert fake_keitaro.calls["delete_stream"] == 1
    assert db_session.query(CampaignLocal).count() == 0


def test_db_commit_failure_rolls_back_remote_objects(
    api_client: TestClient, fake_keitaro: FakeKeitaro, db_session: Session, monkeypatch
):
    def failing_commit():
        raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(db_session, "commit", failing_commit)

    r = api_client.post("/api/campaigns", json=PAYLOAD)
    assert r.status_code == 500
    assert "rolled back" in r.json()["detail"]

    campaign = _only_campaign(fake_keitaro)
    assert campaign["state"] == "archived"
    assert fake_keitaro.streams == {}
    assert fake_keitaro.calls["delete_stream"] == 2
    monkeypatch.undo()
    assert db_session.query(CampaignLocal).count() == 0


def test_cleanup_failure_disables_campaign_and_logs_orphans(
    api_client: TestClient, fake_keitaro: FakeKeitaro, caplog
):
    fake_keitaro.fail("create_stream", KeitaroError("stream boom", status_code=502), on_call=2)
    fake_keitaro.fail("delete_campaign", KeitaroError("archive boom", status_code=502))
    fake_keitaro.fail("delete_stream", KeitaroError("delete boom", status_code=502))

    with caplog.at_level(logging.WARNING, logger="adrobot"):
        r = api_client.post("/api/campaigns", json=PAYLOAD)
    assert r.status_code == 502

    campaign = _only_campaign(fake_keitaro)
    assert campaign["state"] == "disabled"
    leftover_stream_id = next(iter(fake_keitaro.streams))
    orphan_logs = [rec.getMessage() for rec in caplog.records if "ORPHANED" in rec.getMessage()]
    assert len(orphan_logs) == 1
    assert str(campaign["id"]) in orphan_logs[0]
    assert str(leftover_stream_id) in orphan_logs[0]


def _lose_response(fake: FakeKeitaro, method: str, monkeypatch, on_call: int = 1) -> None:
    """Apply the write remotely, then fail as if the connection dropped before the response."""
    original = getattr(fake, method)
    calls = {"n": 0}

    def wrapper(body):
        calls["n"] += 1
        result = original(body)
        if calls["n"] == on_call:
            raise KeitaroError("Server disconnected", status_code=502, transport=True)
        return result

    monkeypatch.setattr(fake, method, wrapper)


def test_lost_campaign_response_is_found_by_alias_and_archived(
    api_client: TestClient, fake_keitaro: FakeKeitaro, db_session: Session, monkeypatch
):
    _lose_response(fake_keitaro, "create_campaign", monkeypatch)

    r = api_client.post("/api/campaigns", json=PAYLOAD)
    assert r.status_code == 502
    assert _only_campaign(fake_keitaro)["state"] == "archived"
    assert fake_keitaro.calls["create_stream"] == 0
    assert db_session.query(CampaignLocal).count() == 0


def test_lost_stream_response_stream_is_discovered_and_deleted(
    api_client: TestClient, fake_keitaro: FakeKeitaro, monkeypatch
):
    _lose_response(fake_keitaro, "create_stream", monkeypatch, on_call=2)

    r = api_client.post("/api/campaigns", json=PAYLOAD)
    assert r.status_code == 502
    assert _only_campaign(fake_keitaro)["state"] == "archived"
    assert fake_keitaro.streams == {}
    assert fake_keitaro.calls["delete_stream"] == 2


def test_campaign_creation_failure_needs_no_cleanup(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    fake_keitaro.fail("create_campaign", KeitaroError("bad domain", status_code=400))

    r = api_client.post("/api/campaigns", json=PAYLOAD)
    assert r.status_code == 400
    assert fake_keitaro.calls["delete_campaign"] == 0
    assert fake_keitaro.calls["create_stream"] == 0


def test_validation_happens_before_remote_writes(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    r = api_client.post("/api/campaigns", json={**PAYLOAD, "offer_id": 999})
    assert r.status_code == 404
    assert fake_keitaro.campaigns == {}

    r = api_client.post("/api/campaigns", json={**PAYLOAD, "geo": "1x"})
    assert r.status_code == 400
    assert fake_keitaro.campaigns == {}


def test_zero_ids_are_not_replaced_by_defaults(api_client: TestClient, fake_keitaro: FakeKeitaro):
    r = api_client.post("/api/campaigns", json={**PAYLOAD, "domain_id": 0, "group_id": None})
    assert r.status_code == 200, r.text
    campaign = r.json()["campaign"]
    assert campaign["domain_id"] == 0
    assert campaign["group_id"] == 10332
