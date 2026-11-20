"""API tests with TestClient and mocked Keitaro (no live network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import FakeKeitaro


def _active_offers(detail: dict, stream_local_id: int) -> list[dict]:
    for s in detail["streams"]:
        if s["id"] == stream_local_id:
            return [o for o in s["offers"] if o["status"] == "active"]
    raise AssertionError(f"stream {stream_local_id} not found")


def _offers_stream(detail: dict) -> dict:
    for s in detail["streams"]:
        if s["has_offers"]:
            return s
    raise AssertionError("no offers stream")


def test_health(api_client: TestClient):
    r = api_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "prefix" in body


def test_meta_domains_groups_sources(api_client: TestClient, fake_keitaro: FakeKeitaro):
    assert api_client.get("/api/meta/domains").json()[0]["id"] == 1813
    assert api_client.get("/api/meta/groups").json()[0]["name"] == "FORTESTS"
    assert api_client.get("/api/meta/traffic-sources").json()[0]["id"] == 836


def test_list_offers_filter(api_client: TestClient):
    all_offers = api_client.get("/api/offers").json()
    assert len(all_offers) >= 3
    filtered = api_client.get("/api/offers", params={"q": "beta"}).json()
    assert len(filtered) == 1
    assert filtered[0]["id"] == 102


def test_create_campaign_builds_two_streams(api_client: TestClient, fake_keitaro: FakeKeitaro):
    r = api_client.post(
        "/api/campaigns",
        json={
            "name": "pytest_unit_create",
            "geo": "au",
            "offer_id": 101,
            "domain_id": 1813,
            "group_id": 10332,
            "traffic_source_id": 836,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    campaign = body["campaign"]
    assert campaign["name"].startswith("test_timovey_")
    assert campaign["geo"] == "AU"
    assert campaign["domain_id"] == 1813
    assert campaign["group_id"] == 10332
    assert campaign["traffic_source_id"] == 836
    assert len(campaign["streams"]) == 2

    geo_stream = next(s for s in campaign["streams"] if not s["has_offers"])
    offers_stream = next(s for s in campaign["streams"] if s["has_offers"])
    assert "Google" in geo_stream["name"]
    assert geo_stream["stream_type"] == "regular"
    assert geo_stream["stream_schema"] == "redirect"
    assert offers_stream["stream_type"] == "default"
    assert offers_stream["offers"][0]["keitaro_offer_id"] == 101
    assert offers_stream["offers"][0]["share"] == 100

    # Fake Keitaro side effects
    remote = fake_keitaro.get_campaign(campaign["keitaro_campaign_id"])
    assert remote["domain_id"] == 1813
    streams = fake_keitaro.list_campaign_streams(campaign["keitaro_campaign_id"])
    assert len(streams) == 2
    google = next(s for s in streams if s["schema"] == "redirect")
    assert google["filters"][0]["payload"] == ["AU"]
    assert google["action_payload"] == "https://www.google.com/"


def test_editor_add_remove_bring_back_pin_push_cancel(
    api_client: TestClient, fake_keitaro: FakeKeitaro
):
    created = api_client.post(
        "/api/campaigns",
        json={
            "name": "pytest_unit_editor",
            "geo": "DE",
            "offer_id": 101,
            "domain_id": 1813,
            "group_id": 10332,
            "traffic_source_id": 836,
        },
    ).json()
    keitaro_id = created["campaign"]["keitaro_campaign_id"]
    stream = _offers_stream(created["campaign"])
    sid = stream["id"]
    remote_sid = stream["keitaro_stream_id"]

    # Fetch streams
    fetched = api_client.post(f"/api/campaigns/{keitaro_id}/fetch-streams").json()
    assert len(fetched["streams"]) == 2

    # Add second offer → equal 50/50, dirty
    detail = api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102}).json()
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is True
    active = {o["keitaro_offer_id"]: o["share"] for o in stream["offers"] if o["status"] == "active"}
    assert active == {101: 50, 102: 50}

    # Add third → ~33 each
    detail = api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 103}).json()
    active_shares = sorted(
        o["share"] for o in _offers_stream(detail)["offers"] if o["status"] == "active"
    )
    assert sum(active_shares) == 100
    assert active_shares[0] in (33, 34)

    # Pin offer 101 at 40
    detail = api_client.post(
        f"/api/streams/{sid}/offers/101/pin",
        json={"pinned": True, "share": 40},
    ).json()
    by_id = {o["keitaro_offer_id"]: o for o in _offers_stream(detail)["offers"]}
    assert by_id[101]["pinned"] is True
    assert by_id[101]["share"] == 40
    assert by_id[102]["share"] + by_id[103]["share"] == 60

    # Remove 103
    detail = api_client.delete(f"/api/streams/{sid}/offers/103").json()
    by_id = {o["keitaro_offer_id"]: o for o in _offers_stream(detail)["offers"]}
    assert by_id[103]["status"] == "removed"
    assert by_id[103]["share"] == 0
    assert by_id[101]["share"] == 40
    assert by_id[102]["share"] == 60

    # Bring back 103
    detail = api_client.post(f"/api/streams/{sid}/offers/103/bring-back").json()
    by_id = {o["keitaro_offer_id"]: o for o in _offers_stream(detail)["offers"]}
    assert by_id[103]["status"] == "active"
    assert by_id[101]["share"] == 40
    assert by_id[102]["share"] + by_id[103]["share"] == 60

    # Push to Keitaro
    detail = api_client.post(f"/api/streams/{sid}/push").json()
    assert _offers_stream(detail)["is_dirty"] is False
    remote = fake_keitaro.get_stream(remote_sid)
    remote_ids = {int(o["offer_id"]) for o in remote["offers"]}
    assert remote_ids == {101, 102, 103}

    # Local draft change then cancel
    detail = api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102}).json()
    # 102 already active — expect 400
    assert True  # already active path covered below
    r = api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 102})
    assert r.status_code == 400

    # Remove then cancel should restore from Keitaro (all three active)
    api_client.delete(f"/api/streams/{sid}/offers/103")
    detail = api_client.post(f"/api/streams/{sid}/cancel").json()
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is False
    active_ids = {o["keitaro_offer_id"] for o in stream["offers"] if o["status"] == "active"}
    assert active_ids == {101, 102, 103}


def test_list_local_campaigns_after_create(api_client: TestClient):
    api_client.post(
        "/api/campaigns",
        json={
            "name": "pytest_list",
            "geo": "US",
            "offer_id": 101,
            "domain_id": 1813,
            "group_id": 10332,
            "traffic_source_id": 836,
        },
    )
    rows = api_client.get("/api/campaigns").json()
    assert len(rows) >= 1
    assert rows[0]["name"].startswith("test_timovey_")


def test_add_offer_on_geo_stream_rejected(api_client: TestClient):
    created = api_client.post(
        "/api/campaigns",
        json={
            "name": "pytest_geo_reject",
            "geo": "FR",
            "offer_id": 101,
            "domain_id": 1813,
            "group_id": 10332,
            "traffic_source_id": 836,
        },
    ).json()
    geo = next(s for s in created["campaign"]["streams"] if not s["has_offers"])
    r = api_client.post(f"/api/streams/{geo['id']}/offers", json={"offer_id": 102})
    assert r.status_code == 400
