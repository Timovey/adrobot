"""
Live integration tests against real Keitaro Admin API.

Opt-in: RUN_LIVE_TESTS=1 pytest -m live
Requires KEITARO_URL / KEITARO_API_KEY in .env (and preferred DEFAULT_* ids).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CampaignLocal
from app.schemas import CreateCampaignRequest
from app.services import campaigns as campaign_service
from app.services.keitaro import KeitaroClient, KeitaroError
from tests.conftest import LIVE_CREATED_CAMPAIGNS

pytestmark = pytest.mark.live


def _unique_name(prefix: str = "test_timovey_pytest_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}_{int(time.time())}"


def _offers_stream(detail: dict) -> dict:
    for s in detail["streams"]:
        if s["has_offers"]:
            return s
    raise AssertionError("offers stream not found")


def _geo_stream(detail: dict) -> dict:
    for s in detail["streams"]:
        if not s["has_offers"]:
            return s
    raise AssertionError("geo stream not found")


def _active_by_id(stream: dict) -> dict[int, dict]:
    return {o["keitaro_offer_id"]: o for o in stream["offers"] if o["status"] == "active"}


def _cleanup_campaign(client: KeitaroClient, campaign_id: int) -> None:
    """Archive (soft-delete) the campaign so the live sandbox stays tidy; disable as fallback."""
    state = "archived"
    try:
        client.delete_campaign(campaign_id)
    except Exception:
        try:
            client.disable_campaign(campaign_id)
            state = "disabled"
        except Exception:
            state = "CLEANUP FAILED"
    LIVE_CREATED_CAMPAIGNS.append((campaign_id, state))


def _remote_campaign_gone_or_inactive(client: KeitaroClient, campaign_id: int) -> bool:
    try:
        remote = client.get_campaign(campaign_id)
    except KeitaroError as e:
        return e.status_code == 404
    return remote.get("state") != "active"


@pytest.fixture()
def created_live_campaign(
    live_api_client: TestClient,
    live_client: KeitaroClient,
    live_settings: Settings,
    live_offer_ids: list[int],
):
    """Create a identifiable test campaign; soft-delete on teardown."""
    name = _unique_name()
    offer_a = live_offer_ids[0]
    payload = {
        "name": name,
        "geo": "AU",
        "offer_id": offer_a,
        "domain_id": live_settings.default_domain_id,
        "group_id": live_settings.default_group_id,
        "traffic_source_id": live_settings.default_traffic_source_id,
    }
    r = live_api_client.post("/api/campaigns", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    campaign_id = body["campaign"]["keitaro_campaign_id"]
    try:
        yield {
            "response": body,
            "campaign_id": campaign_id,
            "name": body["campaign"]["name"],
            "offer_a": offer_a,
            "offer_b": live_offer_ids[1] if len(live_offer_ids) > 1 else offer_a,
            "offer_c": live_offer_ids[2] if len(live_offer_ids) > 2 else live_offer_ids[-1],
        }
    finally:
        _cleanup_campaign(live_client, campaign_id)


def test_live_create_campaign_structure(created_live_campaign: dict[str, Any], live_client: KeitaroClient, live_settings: Settings):
    body = created_live_campaign["response"]
    campaign = body["campaign"]
    campaign_id = created_live_campaign["campaign_id"]

    assert campaign["name"].startswith("test_timovey_")
    assert created_live_campaign["name"].startswith("test_timovey_pytest_")
    assert campaign["geo"] == "AU"
    assert campaign["domain_id"] == live_settings.default_domain_id
    assert campaign["group_id"] == live_settings.default_group_id
    assert campaign["traffic_source_id"] == live_settings.default_traffic_source_id
    assert len(campaign["streams"]) == 2

    # Verify against live Keitaro
    remote = live_client.get_campaign(campaign_id)
    assert remote["name"] == campaign["name"]
    assert int(remote["domain_id"]) == live_settings.default_domain_id
    assert int(remote["group_id"]) == live_settings.default_group_id
    assert int(remote["traffic_source_id"]) == live_settings.default_traffic_source_id

    remote_streams = live_client.list_campaign_streams(campaign_id)
    assert len(remote_streams) == 2

    google = next(s for s in remote_streams if s.get("schema") == "redirect")
    offers = next(s for s in remote_streams if s.get("schema") == "landings")

    assert google.get("type") == "regular"
    filters = google.get("filters") or []
    country = next((f for f in filters if f.get("name") == "country"), None)
    assert country is not None
    assert country.get("mode") == "accept"
    assert "AU" in (country.get("payload") or [])
    assert (google.get("action_payload") or "").startswith("https://www.google.")

    assert offers.get("type") == "default"
    offer_rows = offers.get("offers") or []
    assert len(offer_rows) == 1
    assert int(offer_rows[0]["offer_id"]) == created_live_campaign["offer_a"]
    assert float(offer_rows[0]["share"]) == 100


def test_live_editor_full_flow(
    live_api_client: TestClient,
    live_client: KeitaroClient,
    created_live_campaign: dict[str, Any],
):
    campaign_id = created_live_campaign["campaign_id"]
    offer_a = created_live_campaign["offer_a"]
    offer_b = created_live_campaign["offer_b"]
    offer_c = created_live_campaign["offer_c"]
    # Need two distinct offers for weight checks
    if offer_b == offer_a:
        pytest.skip("Need distinct secondary offer")
    if offer_c == offer_a:
        offer_c = offer_b

    # Sync streams from Keitaro
    detail = live_api_client.post(f"/api/campaigns/{campaign_id}/fetch-streams").json()
    assert len(detail["streams"]) == 2
    geo = _geo_stream(detail)
    stream = _offers_stream(detail)
    sid = stream["id"]
    remote_sid = stream["keitaro_stream_id"]
    assert geo["has_offers"] is False
    assert stream["has_offers"] is True
    assert _active_by_id(stream)[offer_a]["share"] == 100

    # --- Add offer B → 50/50 ---
    detail = live_api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": offer_b}).json()
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is True
    active = _active_by_id(stream)
    assert set(active) == {offer_a, offer_b}
    assert active[offer_a]["share"] == 50
    assert active[offer_b]["share"] == 50

    # --- Add offer C (if distinct) ---
    third_added = False
    if offer_c not in (offer_a, offer_b):
        detail = live_api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": offer_c}).json()
        stream = _offers_stream(detail)
        active = _active_by_id(stream)
        assert set(active) == {offer_a, offer_b, offer_c}
        assert sum(o["share"] for o in active.values()) == 100
        third_added = True

    # --- Pin offer A at 40 ---
    detail = live_api_client.post(
        f"/api/streams/{sid}/offers/{offer_a}/pin",
        json={"pinned": True, "share": 40},
    ).json()
    stream = _offers_stream(detail)
    active = _active_by_id(stream)
    assert active[offer_a]["pinned"] is True
    assert active[offer_a]["share"] == 40
    others = [oid for oid in active if oid != offer_a]
    assert sum(active[oid]["share"] for oid in others) == 60

    # --- Remove offer B ---
    detail = live_api_client.delete(f"/api/streams/{sid}/offers/{offer_b}").json()
    stream = _offers_stream(detail)
    by_all = {o["keitaro_offer_id"]: o for o in stream["offers"]}
    assert by_all[offer_b]["status"] == "removed"
    assert by_all[offer_b]["share"] == 0
    active = _active_by_id(stream)
    assert offer_b not in active
    assert active[offer_a]["share"] == 40 if third_added else 100
    if not third_added:
        # Only A left active (pinned 40 → only pinned normalizes or free empty)
        # With only pinned active, redistribute normalizes pinned to 100
        assert active[offer_a]["share"] == 100
    else:
        assert sum(o["share"] for o in active.values()) == 100

    # --- Bring back B ---
    detail = live_api_client.post(f"/api/streams/{sid}/offers/{offer_b}/bring-back").json()
    stream = _offers_stream(detail)
    active = _active_by_id(stream)
    assert offer_b in active
    assert sum(o["share"] for o in active.values()) == 100
    # With 3+ offers, A stayed pinned at 40 while B was removed; with only 2,
    # remove normalized the sole pinned offer to 100 before bring-back.
    if third_added:
        assert active[offer_a]["share"] == 40

    # --- Push ---
    detail = live_api_client.post(f"/api/streams/{sid}/push").json()
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is False
    remote = live_client.get_stream(remote_sid)
    remote_ids = {int(o["offer_id"]) for o in (remote.get("offers") or [])}
    assert offer_a in remote_ids
    assert offer_b in remote_ids
    if third_added:
        assert offer_c in remote_ids
    # Shares on Keitaro should match local active
    remote_shares = {
        int(o["offer_id"]): float(o.get("share") or 0) for o in (remote.get("offers") or [])
    }
    for oid, local_offer in _active_by_id(stream).items():
        assert remote_shares[oid] == pytest.approx(local_offer["share"], abs=1)

    # --- Draft remove then Cancel restores Keitaro ---
    live_api_client.delete(f"/api/streams/{sid}/offers/{offer_b}")
    detail = live_api_client.post(f"/api/streams/{sid}/cancel").json()
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is False
    active = _active_by_id(stream)
    assert offer_b in active
    assert offer_a in active


def test_live_fetch_mirrors_remote_changes(
    live_api_client: TestClient,
    live_client: KeitaroClient,
    created_live_campaign: dict[str, Any],
):
    campaign_id = created_live_campaign["campaign_id"]
    offer_a = created_live_campaign["offer_a"]
    detail = live_api_client.post(f"/api/campaigns/{campaign_id}/fetch-streams").json()
    offers_stream = _offers_stream(detail)
    geo_remote_sid = _geo_stream(detail)["keitaro_stream_id"]
    assert offers_stream["is_dirty"] is False

    # Remote-only share edit (e.g. someone changed it in Keitaro UI) is mirrored, stays clean.
    live_client.update_stream(
        offers_stream["keitaro_stream_id"],
        {"offers": [{"offer_id": offer_a, "share": 100, "state": "active"}]},
    )
    detail = live_api_client.post(f"/api/campaigns/{campaign_id}/fetch-streams").json()
    stream = _offers_stream(detail)
    assert stream["is_dirty"] is False
    assert _active_by_id(stream)[offer_a]["share"] == 100

    # Stream deleted in Keitaro disappears locally on fetch.
    live_client.delete_stream(geo_remote_sid)
    detail = live_api_client.post(f"/api/campaigns/{campaign_id}/fetch-streams").json()
    assert [s["keitaro_stream_id"] for s in detail["streams"]] == [stream["keitaro_stream_id"]]

    # Push still sends integer shares summing to 100.
    detail = live_api_client.post(f"/api/streams/{stream['id']}/push").json()
    assert _offers_stream(detail)["is_dirty"] is False
    remote = live_client.get_stream(stream["keitaro_stream_id"])
    assert sum(int(o["share"]) for o in remote.get("offers") or []) == 100


class _FailSecondStream:
    """Proxy over the live client that fails the 2nd create_stream call."""

    def __init__(self, inner: KeitaroClient):
        self._inner = inner
        self.campaign_id: int | None = None
        self.stream_ids: list[int] = []

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def create_campaign(self, body: dict) -> dict:
        created = self._inner.create_campaign(body)
        self.campaign_id = int(created["id"])
        return created

    def find_campaign_by_alias(self, alias: str) -> dict | None:
        found = self._inner.find_campaign_by_alias(alias)
        if found:
            self.campaign_id = int(found["id"])
        return found

    def create_stream(self, body: dict) -> dict:
        if self.stream_ids:
            raise KeitaroError("injected failure on second stream", status_code=502)
        created = self._inner.create_stream(body)
        self.stream_ids.append(int(created["id"]))
        return created


def test_live_create_compensation_on_mid_create_failure(
    live_client: KeitaroClient,
    live_settings: Settings,
    live_db_session: Session,
    live_offer_ids: list[int],
):
    proxy = _FailSecondStream(live_client)
    payload = CreateCampaignRequest(
        name=_unique_name("test_timovey_pytest_comp_"),
        geo="AU",
        offer_id=live_offer_ids[0],
        domain_id=live_settings.default_domain_id,
        group_id=live_settings.default_group_id,
        traffic_source_id=live_settings.default_traffic_source_id,
    )
    try:
        with pytest.raises(KeitaroError, match="injected failure"):
            campaign_service.create_campaign(live_db_session, proxy, live_settings, payload)  # type: ignore[arg-type]

        assert proxy.campaign_id is not None
        assert len(proxy.stream_ids) == 1
        assert _remote_campaign_gone_or_inactive(live_client, proxy.campaign_id)
        # Keitaro soft-deletes streams: GET still works but reports state "deleted".
        try:
            stream_state = live_client.get_stream(proxy.stream_ids[0]).get("state")
        except KeitaroError as e:
            assert e.status_code == 404
        else:
            assert stream_state == "deleted"
        assert live_db_session.query(CampaignLocal).count() == 0
    finally:
        if proxy.campaign_id is not None:
            if _remote_campaign_gone_or_inactive(live_client, proxy.campaign_id):
                LIVE_CREATED_CAMPAIGNS.append((proxy.campaign_id, "archived by compensation"))
            else:
                _cleanup_campaign(live_client, proxy.campaign_id)


def test_live_meta_and_offers_discoverable(live_api_client: TestClient, live_settings: Settings):
    domains = live_api_client.get("/api/meta/domains").json()
    groups = live_api_client.get("/api/meta/groups").json()
    sources = live_api_client.get("/api/meta/traffic-sources").json()
    offers = live_api_client.get("/api/offers", params={"limit": 20}).json()

    assert any(d["id"] == live_settings.default_domain_id for d in domains)
    assert any(g["id"] == live_settings.default_group_id for g in groups)
    assert any(s["id"] == live_settings.default_traffic_source_id for s in sources)
    assert len(offers) >= 1
