"""Upstream error mapping, local AppError handling and optional API token auth."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, get_client
from app.services.keitaro import KeitaroClient, KeitaroError


def _client_with_transport(settings: Settings, handler) -> KeitaroClient:
    return KeitaroClient(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda req: httpx.ConnectError("connection refused", request=req),
        lambda req: httpx.ReadTimeout("read timed out", request=req),
        lambda req: httpx.ConnectTimeout("connect timed out", request=req),
    ],
    ids=["connect-error", "read-timeout", "connect-timeout"],
)
def test_transport_errors_become_502_json(
    api_client: TestClient, test_settings: Settings, exc_factory, caplog
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)

    keitaro = _client_with_transport(test_settings, handler)
    app.dependency_overrides[get_client] = lambda: keitaro

    with caplog.at_level(logging.DEBUG, logger="adrobot"):
        r = api_client.get("/api/meta/domains")
    assert r.status_code == 502
    assert r.headers["content-type"].startswith("application/json")
    assert "unreachable" in r.json()["detail"]
    assert test_settings.keitaro_api_key not in r.text
    assert test_settings.keitaro_api_key not in caplog.text


def test_client_raises_keitaro_error_on_transport_failure(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    with pytest.raises(KeitaroError) as info:
        _client_with_transport(test_settings, handler).list_domains()
    assert info.value.status_code == 502
    assert info.value.transport is True


@pytest.mark.parametrize(
    "exc_type", [httpx.RemoteProtocolError, httpx.ConnectError, httpx.ConnectTimeout]
)
def test_get_retried_once_after_dropped_connection(test_settings: Settings, exc_type):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            raise exc_type("dropped", request=request)
        return httpx.Response(200, json=[{"id": 1}])

    assert _client_with_transport(test_settings, handler).list_domains() == [{"id": 1}]
    assert calls == ["GET", "GET"]


def test_writes_and_timeouts_are_not_retried(test_settings: Settings):
    calls: list[str] = []

    def dropped(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.RemoteProtocolError("Server disconnected", request=request)

    with pytest.raises(KeitaroError):
        _client_with_transport(test_settings, dropped).create_campaign({"name": "x"})
    assert calls == ["POST"]

    calls.clear()

    def slow(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(KeitaroError):
        _client_with_transport(test_settings, slow).list_domains()
    assert calls == ["GET"]


@pytest.mark.parametrize(
    ("upstream", "expected"),
    [(404, 404), (400, 400), (401, 502), (403, 502), (500, 502), (503, 502)],
)
def test_upstream_status_mapping(api_client: TestClient, test_settings: Settings, upstream, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(upstream, json={"error": "upstream"})

    app.dependency_overrides[get_client] = lambda: _client_with_transport(test_settings, handler)
    r = api_client.get("/api/meta/domains")
    assert r.status_code == expected
    assert "upstream" in r.json()["detail"]


def test_invalid_json_from_upstream_is_502(api_client: TestClient, test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy error</html>")

    app.dependency_overrides[get_client] = lambda: _client_with_transport(test_settings, handler)
    assert api_client.get("/api/meta/domains").status_code == 502


def test_client_sends_api_key_header(test_settings: Settings):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Api-Key")
        return httpx.Response(200, json=[])

    _client_with_transport(test_settings, handler).list_domains()
    assert seen["key"] == "test-key"


def test_local_not_found_is_404_json(api_client: TestClient):
    r = api_client.post("/api/streams/99999/offers", json={"offer_id": 101})
    assert r.status_code == 404
    assert r.json() == {"detail": "Local stream 99999 not found"}


def test_missing_keitaro_key_is_500_json(api_client: TestClient, test_settings: Settings):
    app.dependency_overrides.pop(get_client)
    test_settings.keitaro_api_key = ""
    r = api_client.get("/api/meta/domains")
    assert r.status_code == 500
    assert "KEITARO_API_KEY" in r.json()["detail"]


# --- auth -----------------------------------------------------------------


def test_auth_not_required_when_token_unset(api_client: TestClient):
    assert api_client.get("/api/campaigns").status_code == 200
    assert api_client.get("/api/health").json()["auth_required"] is False


def test_auth_required_when_token_set(api_client: TestClient, test_settings: Settings):
    test_settings.adrobot_api_token = "s3cret-token"

    r = api_client.get("/api/campaigns")
    assert r.status_code == 401
    assert r.json() == {"detail": "Missing or invalid API token"}
    assert r.headers["www-authenticate"] == "Bearer"

    assert api_client.get("/api/campaigns", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api_client.get("/api/campaigns", headers={"X-API-Key": "wrong"}).status_code == 401
    assert api_client.get("/api/campaigns", headers={"Authorization": "Basic s3cret-token"}).status_code == 401

    assert api_client.get("/api/campaigns", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    assert api_client.get("/api/campaigns", headers={"X-API-Key": "s3cret-token"}).status_code == 200
    assert (
        api_client.post(
            "/api/streams/1/push", headers={"Authorization": "Bearer s3cret-token"}
        ).status_code
        == 404
    )
    assert api_client.post("/api/streams/1/push").status_code == 401


def test_health_is_public_when_token_set(api_client: TestClient, test_settings: Settings):
    test_settings.adrobot_api_token = "s3cret-token"
    r = api_client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["auth_required"] is True
