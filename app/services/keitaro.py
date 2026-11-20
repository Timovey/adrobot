from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("adrobot.keitaro")


class KeitaroError(Exception):
    """Error talking to the upstream Keitaro Admin API."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: Any = None,
        *,
        transport: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        # True when no HTTP response was received: a write may or may not have been applied.
        self.transport = transport


# Safe to resend only for reads; Keitaro sometimes drops the connection without a response
# or stalls the TLS handshake.
_RETRYABLE_TRANSPORT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)


def create_http_client(settings: Settings) -> httpx.Client:
    timeout = settings.keitaro_timeout_seconds
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=min(20.0, timeout)))


def _upstream_status(status: int) -> int:
    # 401/403 from Keitaro mean our server-side API key is wrong; surfacing them
    # as-is would look like the caller failed Adrobot's own auth. 5xx -> 502.
    if status in (401, 403) or status >= 500:
        return 502
    return status


class KeitaroClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.base = settings.keitaro_api_base
        self._headers = {
            "Api-Key": settings.keitaro_api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._owns_http = http is None
        self._http = http or create_http_client(settings)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _request(self, method: str, path: str, json_body: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        started = time.perf_counter()
        attempts = 2 if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.request(method, url, headers=self._headers, json=json_body)
                break
            except httpx.HTTPError as e:
                logger.warning(
                    "Keitaro %s %s transport error (attempt %d/%d): %s: %s",
                    method,
                    path,
                    attempt,
                    attempts,
                    type(e).__name__,
                    e,
                )
                if attempt < attempts and isinstance(e, _RETRYABLE_TRANSPORT_ERRORS):
                    continue
                raise KeitaroError(
                    f"Keitaro API {method} {path} unreachable: {type(e).__name__}: {e}",
                    status_code=502,
                    transport=True,
                ) from e
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug("Keitaro %s %s -> %s (%.0f ms)", method, path, response.status_code, elapsed_ms)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            logger.warning(
                "Keitaro %s %s failed: %s %s", method, path, response.status_code, str(payload)[:500]
            )
            raise KeitaroError(
                f"Keitaro API {method} {path} failed: {response.status_code} {payload}",
                status_code=_upstream_status(response.status_code),
                payload=payload,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as e:
            logger.warning("Keitaro %s %s returned non-JSON body", method, path)
            raise KeitaroError(f"Keitaro API {method} {path} returned invalid JSON", status_code=502) from e

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._request("POST", path, body or {})

    def put(self, path: str, body: dict | None = None) -> Any:
        return self._request("PUT", path, body or {})

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # --- entities ---
    def find_campaign_by_alias(self, alias: str) -> dict | None:
        return next((c for c in self.list_campaigns() if c.get("alias") == alias), None)

    def list_campaigns(self, limit: int | None = None, offset: int | None = None) -> list[dict]:
        params = []
        if limit is not None:
            params.append(f"limit={limit}")
        if offset is not None:
            params.append(f"offset={offset}")
        qs = ("?" + "&".join(params)) if params else ""
        return self.get(f"/campaigns{qs}")

    def get_campaign(self, campaign_id: int) -> dict:
        return self.get(f"/campaigns/{campaign_id}")

    def create_campaign(self, body: dict) -> dict:
        return self.post("/campaigns", body)

    def update_campaign(self, campaign_id: int, body: dict) -> dict:
        return self.put(f"/campaigns/{campaign_id}", body)

    def delete_campaign(self, campaign_id: int) -> Any:
        """Keitaro moves the campaign to the archive."""
        return self.delete(f"/campaigns/{campaign_id}")

    def disable_campaign(self, campaign_id: int) -> Any:
        return self.post(f"/campaigns/{campaign_id}/disable")

    def list_campaign_streams(self, campaign_id: int) -> list[dict]:
        return self.get(f"/campaigns/{campaign_id}/streams")

    def create_stream(self, body: dict) -> dict:
        return self.post("/streams", body)

    def update_stream(self, stream_id: int, body: dict) -> dict:
        return self.put(f"/streams/{stream_id}", body)

    def get_stream(self, stream_id: int) -> dict:
        return self.get(f"/streams/{stream_id}")

    def delete_stream(self, stream_id: int) -> Any:
        return self.delete(f"/streams/{stream_id}")

    def list_offers(self) -> list[dict]:
        # Admin API v1 GET /offers has no search/pagination parameters.
        return self.get("/offers")

    def get_offer(self, offer_id: int) -> dict:
        return self.get(f"/offers/{offer_id}")

    def list_domains(self) -> list[dict]:
        return self.get("/domains")

    def list_groups(self, group_type: str = "campaigns") -> list[dict]:
        return self.get(f"/groups?type={group_type}")

    def list_traffic_sources(self) -> list[dict]:
        return self.get("/traffic_sources")


def slugify_alias(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    if not base:
        base = "campaign"
    suffix = uuid.uuid4().hex[:8]
    return f"{base[:40]}-{suffix}"


def resolve_defaults(client: KeitaroClient, settings: Settings) -> tuple[int, int, int]:
    """Pick domain / group / traffic source from settings or first available via API."""
    domain_id = settings.default_domain_id
    group_id = settings.default_group_id
    source_id = settings.default_traffic_source_id

    if domain_id is None:
        domains = [d for d in client.list_domains() if d.get("state") != "deleted"]
        if not domains:
            raise KeitaroError("No domains available in Keitaro", status_code=502)
        domain_id = int(domains[0]["id"])

    if group_id is None:
        groups = client.list_groups("campaigns")
        if not groups:
            raise KeitaroError("No campaign groups available in Keitaro", status_code=502)
        # Prefer FORTESTS group if present
        preferred = next((g for g in groups if str(g.get("name", "")).upper() == "FORTESTS"), groups[0])
        group_id = int(preferred["id"])

    if source_id is None:
        sources = [s for s in client.list_traffic_sources() if s.get("state") != "deleted"]
        if not sources:
            raise KeitaroError("No traffic sources available in Keitaro", status_code=502)
        preferred = next((s for s in sources if str(s.get("name", "")).upper() == "FORTESTS"), sources[0])
        source_id = int(preferred["id"])

    return domain_id, group_id, source_id
