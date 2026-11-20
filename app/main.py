import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db, init_db
from app.errors import AppError
from app.schemas import (
    AddOfferRequest,
    CampaignDetail,
    CampaignSummary,
    CreateCampaignRequest,
    CreatedCampaignResponse,
    DomainOut,
    GroupOut,
    OfferOut,
    PinOfferRequest,
    TrafficSourceOut,
)
from app.services import campaigns as campaign_service
from app.services.keitaro import KeitaroClient, KeitaroError, create_http_client

logger = logging.getLogger("adrobot.api")

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

AUTH_EXEMPT_PATHS = {"/api/health"}


def _configure_logging(level: str) -> None:
    app_logger = logging.getLogger("adrobot")
    app_logger.setLevel(level.upper())
    if not app_logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _configure_logging(settings.log_level)
    init_db()
    app.state.http_client = create_http_client(settings)
    logger.info(
        "Adrobot started (keitaro=%s, auth=%s)",
        settings.keitaro_url,
        "on" if settings.adrobot_api_token else "off",
    )
    try:
        yield
    finally:
        app.state.http_client.close()


def require_api_token(request: Request, settings: Settings = Depends(get_settings)) -> None:
    token = settings.adrobot_api_token
    path = request.url.path
    if not token or not path.startswith("/api/") or path in AUTH_EXEMPT_PATHS:
        return
    supplied = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization") or ""
    scheme, _, value = auth.partition(" ")
    if not supplied and scheme.lower() == "bearer":
        supplied = value.strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


app = FastAPI(
    title="Adrobot",
    description="Keitaro campaign creator & editor",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_api_token)],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("%s %s failed: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(KeitaroError)
async def keitaro_error_handler(request: Request, exc: KeitaroError) -> JSONResponse:
    status = exc.status_code or 502
    logger.warning("%s %s upstream error (%s): %s", request.method, request.url.path, status, exc)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


def get_client(request: Request, settings: Settings = Depends(get_settings)) -> KeitaroClient:
    if not settings.keitaro_api_key:
        raise AppError("KEITARO_API_KEY is not set in .env", status_code=500)
    return KeitaroClient(settings, http=getattr(request.app.state, "http_client", None))


def _frontend_index() -> Path:
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=503,
            detail="Frontend not built. From repo root run: cd frontend && npm install && npm run build",
        )
    return index


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_frontend_index())


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    path = FRONTEND_DIST / "favicon.svg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(path)


@app.get("/api/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "ok": True,
        "keitaro_url": settings.keitaro_url,
        "prefix": settings.campaign_name_prefix,
        "auth_required": bool(settings.adrobot_api_token),
    }


@app.get("/api/meta/domains", response_model=list[DomainOut])
def meta_domains(client: KeitaroClient = Depends(get_client)) -> list[DomainOut]:
    items = client.list_domains()
    return [
        DomainOut(id=int(i["id"]), name=i.get("name") or str(i["id"]), state=i.get("state"))
        for i in items
        if i.get("state") != "deleted"
    ]


@app.get("/api/meta/groups", response_model=list[GroupOut])
def meta_groups(client: KeitaroClient = Depends(get_client)) -> list[GroupOut]:
    items = client.list_groups("campaigns")
    return [GroupOut(id=int(i["id"]), name=i.get("name") or str(i["id"]), type=i.get("type")) for i in items]


@app.get("/api/meta/traffic-sources", response_model=list[TrafficSourceOut])
def meta_sources(client: KeitaroClient = Depends(get_client)) -> list[TrafficSourceOut]:
    items = client.list_traffic_sources()
    return [
        TrafficSourceOut(id=int(i["id"]), name=i.get("name") or str(i["id"]), state=i.get("state"))
        for i in items
        if i.get("state") != "deleted"
    ]


@app.get("/api/offers", response_model=list[OfferOut])
def list_offers(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    client: KeitaroClient = Depends(get_client),
) -> list[OfferOut]:
    # Keitaro Admin API v1 GET /offers has no search or pagination parameters,
    # so filtering and limiting happen here.
    items = client.list_offers()
    result: list[OfferOut] = []
    q_norm = (q or "").strip().lower()
    for i in items:
        if i.get("state") == "deleted":
            continue
        name = i.get("name") or str(i["id"])
        if q_norm and q_norm not in name.lower() and q_norm not in str(i["id"]):
            continue
        result.append(
            OfferOut(
                id=int(i["id"]),
                name=name,
                state=i.get("state"),
                group_id=i.get("group_id"),
            )
        )
        if len(result) >= limit:
            break
    return result


@app.post("/api/campaigns", response_model=CreatedCampaignResponse)
def create_campaign(
    payload: CreateCampaignRequest,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> CreatedCampaignResponse:
    data = campaign_service.create_campaign(db, client, settings, payload)
    return CreatedCampaignResponse(**data)


@app.get("/api/campaigns", response_model=list[CampaignSummary])
def list_campaigns(db: Session = Depends(get_db)) -> list[CampaignSummary]:
    return campaign_service.list_local_campaigns(db)


@app.get("/api/campaigns/keitaro")
def list_keitaro_campaigns(
    limit: int = Query(default=50, ge=1, le=200),
    client: KeitaroClient = Depends(get_client),
) -> list[dict]:
    items = client.list_campaigns(limit=limit)
    return [
        {
            "id": i.get("id"),
            "name": i.get("name"),
            "alias": i.get("alias"),
            "state": i.get("state"),
            "domain_id": i.get("domain_id"),
            "group_id": i.get("group_id"),
            "traffic_source_id": i.get("traffic_source_id"),
        }
        for i in items
    ]


@app.get("/api/campaigns/{keitaro_campaign_id}", response_model=CampaignDetail)
def get_campaign(
    keitaro_campaign_id: int,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_client),
) -> CampaignDetail:
    local = campaign_service.get_or_create_local_campaign(db, client, keitaro_campaign_id)
    remote = client.get_campaign(keitaro_campaign_id)
    return campaign_service.campaign_to_detail(db, local, keitaro=remote)


@app.post("/api/campaigns/{keitaro_campaign_id}/fetch-streams", response_model=CampaignDetail)
def fetch_streams(
    keitaro_campaign_id: int,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_client),
) -> CampaignDetail:
    return campaign_service.fetch_streams(db, client, keitaro_campaign_id)


@app.post("/api/streams/{stream_local_id}/offers", response_model=CampaignDetail)
def add_offer(
    stream_local_id: int,
    payload: AddOfferRequest,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_client),
) -> CampaignDetail:
    return campaign_service.add_offer(db, client, stream_local_id, payload)


@app.delete("/api/streams/{stream_local_id}/offers/{offer_id}", response_model=CampaignDetail)
def remove_offer(
    stream_local_id: int,
    offer_id: int,
    db: Session = Depends(get_db),
) -> CampaignDetail:
    return campaign_service.remove_offer(db, stream_local_id, offer_id)


@app.post("/api/streams/{stream_local_id}/offers/{offer_id}/bring-back", response_model=CampaignDetail)
def bring_back(
    stream_local_id: int,
    offer_id: int,
    db: Session = Depends(get_db),
) -> CampaignDetail:
    return campaign_service.bring_back_offer(db, stream_local_id, offer_id)


@app.post("/api/streams/{stream_local_id}/offers/{offer_id}/pin", response_model=CampaignDetail)
def pin_offer(
    stream_local_id: int,
    offer_id: int,
    payload: PinOfferRequest,
    db: Session = Depends(get_db),
) -> CampaignDetail:
    return campaign_service.pin_offer(db, stream_local_id, offer_id, payload)


@app.post("/api/streams/{stream_local_id}/push", response_model=CampaignDetail)
def push_stream(
    stream_local_id: int,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_client),
) -> CampaignDetail:
    return campaign_service.push_stream(db, client, stream_local_id)


@app.post("/api/streams/{stream_local_id}/cancel", response_model=CampaignDetail)
def cancel_stream(
    stream_local_id: int,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_client),
) -> CampaignDetail:
    return campaign_service.cancel_stream(db, client, stream_local_id)


# Vite build assets (hashed JS/CSS). Mounted last so /api routes stay primary.
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="frontend-assets")
