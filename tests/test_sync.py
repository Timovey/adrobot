"""Fetch / push / cancel sync semantics against the fake Keitaro (no network)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import CampaignLocal, StreamLocal, StreamOfferLocal
from app.services import campaigns as campaign_service
from tests.conftest import FakeKeitaro


def _create(api_client: TestClient, name: str = "pytest_sync") -> dict:
    r = api_client.post(
        "/api/campaigns",
        json={
            "name": name,
            "geo": "AU",
            "offer_id": 101,
            "domain_id": 1813,
            "group_id": 10332,
            "traffic_source_id": 836,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


def _offers_stream(detail: dict) -> dict:
    return next(s for s in detail["streams"] if s["has_offers"])


def _offers(stream: dict) -> dict[int, tuple[str, float]]:
    return {o["keitaro_offer_id"]: (o["status"], o["share"]) for o in stream["offers"]}


def _set_remote_offers(fake: FakeKeitaro, remote_sid: int, shares: dict[int, int]) -> None:
    fake.streams[remote_sid]["offers"] = [
        {"offer_id": oid, "share": share, "state": "active"} for oid, share in shares.items()
    ]


def test_fetch_mirrors_offer_removed_remotely_and_keeps_syncing(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    campaign = _create(api_client)
    kid = campaign["keitaro_campaign_id"]
    remote_sid = _offers_stream(campaign)["keitaro_stream_id"]

    _set_remote_offers(fake_keitaro, remote_sid, {101: 50, 102: 50})
    stream = _offers_stream(api_client.post(f"/api/campaigns/{kid}/fetch-streams").json())
    assert stream["is_dirty"] is False
    assert _offers(stream) == {101: ("active", 50), 102: ("active", 50)}

    # 102 removed in Keitaro → archived locally, stream stays clean, shares synced
    _set_remote_offers(fake_keitaro, remote_sid, {101: 100})
    stream = _offers_stream(api_client.post(f"/api/campaigns/{kid}/fetch-streams").json())
    assert stream["is_dirty"] is False
    assert _offers(stream) == {101: ("active", 100), 102: ("removed", 0)}

    # Subsequent remote share changes are still mirrored (old bug: frozen once dirty)
    _set_remote_offers(fake_keitaro, remote_sid, {101: 60, 103: 40})
    stream = _offers_stream(api_client.post(f"/api/campaigns/{kid}/fetch-streams").json())
    assert stream["is_dirty"] is False
    assert _offers(stream) == {101: ("active", 60), 102: ("removed", 0), 103: ("active", 40)}

    # Offer re-added remotely becomes active again
    _set_remote_offers(fake_keitaro, remote_sid, {101: 70, 102: 30})
    stream = _offers_stream(api_client.post(f"/api/campaigns/{kid}/fetch-streams").json())
    assert stream["is_dirty"] is False
    assert _offers(stream) == {101: ("active", 70), 102: ("active", 30), 103: ("removed", 0)}


def test_fetch_keeps_real_local_draft(api_client: TestClient, fake_keitaro: FakeKeitaro):
    campaign = _create(api_client)
    kid = campaign["keitaro_campaign_id"]
    sid = _offers_stream(campaign)["id"]

    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    stream = _offers_stream(api_client.post(f"/api/campaigns/{kid}/fetch-streams").json())
    assert stream["is_dirty"] is True
    assert _offers(stream) == {101: ("active", 50), 102: ("active", 50)}


def test_fetch_clears_dirty_flag_when_draft_equals_remote(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    campaign = _create(api_client)
    kid = campaign["keitaro_campaign_id"]
    sid = _offers_stream(campaign)["id"]

    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    detail = api_client.delete(f"/api/streams/{sid}/offers/102").json()
    assert _offers_stream(detail)["is_dirty"] is True  # edited, but back to Keitaro state

    stream = _offers_stream(api_client.post(f"/api/campaigns/{kid}/fetch-streams").json())
    assert stream["is_dirty"] is False
    assert _offers(stream)[101] == ("active", 100)


def test_fetch_removes_streams_deleted_remotely_and_adds_new(
    api_client: TestClient, fake_keitaro: FakeKeitaro, db_session: Session
):
    campaign = _create(api_client)
    kid = campaign["keitaro_campaign_id"]
    geo_remote_sid = next(s for s in campaign["streams"] if not s["has_offers"])["keitaro_stream_id"]

    del fake_keitaro.streams[geo_remote_sid]
    new_remote = fake_keitaro.create_stream(
        {"campaign_id": kid, "name": "Extra", "type": "regular", "schema": "landings",
         "offers": [{"offer_id": 103, "share": 100, "state": "active"}]}
    )

    detail = api_client.post(f"/api/campaigns/{kid}/fetch-streams").json()
    remote_ids = {s["keitaro_stream_id"] for s in detail["streams"]}
    assert geo_remote_sid not in remote_ids
    assert new_remote["id"] in remote_ids
    assert len(detail["streams"]) == 2

    db_session.expire_all()
    assert db_session.query(StreamLocal).filter_by(keitaro_stream_id=geo_remote_sid).count() == 0
    extra = next(s for s in detail["streams"] if s["keitaro_stream_id"] == new_remote["id"])
    assert extra["stream_schema"] == "landings"
    assert _offers(extra) == {103: ("active", 100)}


def test_fetch_removes_dirty_stream_deleted_remotely(
    api_client: TestClient, fake_keitaro: FakeKeitaro, db_session: Session
):
    campaign = _create(api_client)
    kid = campaign["keitaro_campaign_id"]
    stream = _offers_stream(campaign)
    api_client.post(f"/api/streams/{stream['id']}/offers", json={"offer_id": 102})

    del fake_keitaro.streams[stream["keitaro_stream_id"]]
    detail = api_client.post(f"/api/campaigns/{kid}/fetch-streams").json()
    assert [s["has_offers"] for s in detail["streams"]] == [False]
    db_session.expire_all()
    assert db_session.query(StreamOfferLocal).count() == 0


def test_push_three_offers_sends_integer_shares_summing_100(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    campaign = _create(api_client)
    sid = _offers_stream(campaign)["id"]
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 103})

    detail = api_client.post(f"/api/streams/{sid}/push").json()
    sent = fake_keitaro.update_stream_bodies[-1]["offers"]
    assert [(o["offer_id"], o["share"]) for o in sent] == [(101, 34), (102, 33), (103, 33)]
    assert all(type(o["share"]) is int for o in sent)
    assert _offers(_offers_stream(detail)) == {
        101: ("active", 34), 102: ("active", 33), 103: ("active", 33)
    }


def test_push_with_fractional_pin_sends_integers_summing_100(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    campaign = _create(api_client)
    sid = _offers_stream(campaign)["id"]
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 103})
    api_client.post(f"/api/streams/{sid}/offers/101/pin", json={"pinned": True, "share": 33.5})

    api_client.post(f"/api/streams/{sid}/push")
    sent = {o["offer_id"]: o["share"] for o in fake_keitaro.update_stream_bodies[-1]["offers"]}
    assert sent == {101: 34, 102: 33, 103: 33}


def test_push_normalizes_corrupted_local_shares(
    api_client: TestClient, fake_keitaro: FakeKeitaro, db_session: Session
):
    campaign = _create(api_client)
    sid = _offers_stream(campaign)["id"]
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 103})
    # Simulate legacy rows whose rounded shares don't add up (e.g. 33.5 each → 34*3 = 102)
    for o in db_session.query(StreamOfferLocal).filter_by(stream_id=sid):
        o.share = 33.5
    db_session.commit()

    detail = api_client.post(f"/api/streams/{sid}/push").json()
    sent = [o["share"] for o in fake_keitaro.update_stream_bodies[-1]["offers"]]
    assert sent == [34, 33, 33]
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is False
    assert sum(o["share"] for o in stream["offers"] if o["status"] == "active") == 100


def test_push_failure_keeps_draft(api_client: TestClient, fake_keitaro: FakeKeitaro):
    from app.services.keitaro import KeitaroError

    campaign = _create(api_client)
    sid = _offers_stream(campaign)["id"]
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    fake_keitaro.fail("update_stream", KeitaroError("boom", status_code=502))

    r = api_client.post(f"/api/streams/{sid}/push")
    assert r.status_code == 502
    detail = api_client.get(f"/api/campaigns/{campaign['keitaro_campaign_id']}").json()
    assert _offers_stream(detail)["is_dirty"] is True


def test_cancel_drops_draft_only_offers_and_clears_pins(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    campaign = _create(api_client)
    sid = _offers_stream(campaign)["id"]
    api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    api_client.post(f"/api/streams/{sid}/offers/101/pin", json={"pinned": True, "share": 70})

    stream = _offers_stream(api_client.post(f"/api/streams/{sid}/cancel").json())
    assert stream["is_dirty"] is False
    assert [(o["keitaro_offer_id"], o["share"], o["pinned"]) for o in stream["offers"]] == [
        (101, 100, False)
    ]


def test_get_or_create_local_campaign_integrity_race(
    db_session: Session, fake_keitaro: FakeKeitaro, monkeypatch
):
    remote = fake_keitaro.create_campaign({"name": "test_timovey_race"})
    kid = remote["id"]
    # Row inserted by a "concurrent" request after our existence check.
    db_session.add(CampaignLocal(keitaro_campaign_id=kid, name="concurrent"))
    db_session.commit()

    real_lookup = campaign_service.get_local_by_keitaro_id
    calls = {"n": 0}

    def stale_first_lookup(db, keitaro_campaign_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_lookup(db, keitaro_campaign_id)

    monkeypatch.setattr(campaign_service, "get_local_by_keitaro_id", stale_first_lookup)

    local = campaign_service.get_or_create_local_campaign(db_session, fake_keitaro, kid)
    assert local.name == "concurrent"
    assert db_session.query(CampaignLocal).filter_by(keitaro_campaign_id=kid).count() == 1
