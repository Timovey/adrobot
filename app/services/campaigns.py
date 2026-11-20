from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.config import Settings
from app.errors import AppError
from app.models import CampaignLocal, StreamLocal, StreamOfferLocal, utcnow
from app.schemas import (
    AddOfferRequest,
    CampaignDetail,
    CampaignSummary,
    CreateCampaignRequest,
    PinOfferRequest,
    StreamOfferView,
    StreamView,
)
from app.services.keitaro import KeitaroClient, KeitaroError, resolve_defaults, slugify_alias
from app.services.weights import normalize_to_100, redistribute

logger = logging.getLogger("adrobot.campaigns")


def ensure_prefixed_name(name: str, prefix: str) -> str:
    name = name.strip()
    if name.startswith(prefix):
        return name
    # Allow names that already start with test_
    if name.lower().startswith("test_"):
        return name
    return f"{prefix}{name}"


def _compensate_create(client: KeitaroClient, campaign_id: int, stream_ids: list[int]) -> None:
    """Best-effort rollback of Keitaro objects created by a failed create_campaign."""
    stream_ids = list(stream_ids)
    try:
        # Also catch streams whose create response was lost (created remotely, id unknown).
        for s in client.list_campaign_streams(campaign_id):
            if int(s["id"]) not in stream_ids:
                stream_ids.append(int(s["id"]))
    except Exception as e:  # noqa: BLE001
        logger.warning("Compensation: could not list streams of campaign %s: %s", campaign_id, e)

    failed_streams: list[int] = []
    for sid in reversed(stream_ids):
        try:
            client.delete_stream(sid)
        except Exception as e:  # noqa: BLE001 - best effort
            logger.warning("Compensation: failed to delete stream %s: %s", sid, e)
            failed_streams.append(sid)

    campaign_state = "archived"
    try:
        client.delete_campaign(campaign_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Compensation: failed to archive campaign %s: %s", campaign_id, e)
        try:
            client.disable_campaign(campaign_id)
            campaign_state = "disabled"
        except Exception as e2:  # noqa: BLE001
            logger.warning("Compensation: failed to disable campaign %s: %s", campaign_id, e2)
            campaign_state = "active"

    if campaign_state == "archived" and not failed_streams:
        logger.warning(
            "Rolled back Keitaro campaign %s (archived) and streams %s", campaign_id, stream_ids
        )
    else:
        logger.error(
            "ORPHANED Keitaro objects after failed create: campaign_id=%s (%s), stream_ids=%s; "
            "clean up manually",
            campaign_id,
            campaign_state,
            failed_streams,
        )


def _find_created_campaign_id(client: KeitaroClient, alias: str) -> int | None:
    try:
        found = client.find_campaign_by_alias(alias)
    except Exception as e:  # noqa: BLE001
        logger.error("Could not check for orphaned campaign with alias %r: %s", alias, e)
        return None
    if found is None:
        return None
    logger.warning("Campaign %s was created despite the failed response", found["id"])
    return int(found["id"])


def create_campaign(
    db: Session,
    client: KeitaroClient,
    settings: Settings,
    payload: CreateCampaignRequest,
) -> dict:
    # 1. Validate everything before creating anything remotely.
    geo = payload.geo.strip().upper()
    if len(geo) != 2 or not geo.isalpha():
        raise AppError(f"Invalid geo code {payload.geo!r}; expected 2-letter ISO code", status_code=400)
    name = ensure_prefixed_name(payload.name, settings.campaign_name_prefix)
    alias = slugify_alias(name)

    domain_id = payload.domain_id
    group_id = payload.group_id
    source_id = payload.traffic_source_id
    if domain_id is None or group_id is None or source_id is None:
        d, g, s = resolve_defaults(client, settings)
        domain_id = d if domain_id is None else domain_id
        group_id = g if group_id is None else group_id
        source_id = s if source_id is None else source_id

    offer = client.get_offer(payload.offer_id)
    offer_name = offer.get("name") or str(payload.offer_id)

    # 2. Remote objects, then 3. local DB; compensate remote on any failure.
    campaign_id: int | None = None
    stream_ids: list[int] = []
    try:
        try:
            campaign = client.create_campaign(
                {
                    "name": name,
                    "alias": alias,
                    "type": "position",
                    "state": "active",
                    "domain_id": domain_id,
                    "group_id": group_id,
                    "traffic_source_id": source_id,
                }
            )
        except KeitaroError as e:
            if e.transport:
                # No response: Keitaro may still have created it. The alias is unique.
                campaign_id = _find_created_campaign_id(client, alias)
            raise
        campaign_id = int(campaign["id"])

        google_stream = client.create_stream(
            {
                "campaign_id": campaign_id,
                "name": f"Geo {geo} → Google",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": settings.google_redirect_url,
                "position": 1,
                "collect_clicks": True,
                "filters": [
                    {
                        "name": "country",
                        "mode": "accept",
                        "payload": [geo],
                    }
                ],
                "offers": [],
            }
        )
        stream_ids.append(int(google_stream["id"]))

        offers_stream = client.create_stream(
            {
                "campaign_id": campaign_id,
                "name": "Offers",
                "type": "default",
                "schema": "landings",
                "action_type": "http",
                "action_payload": "",
                "position": 2,
                "collect_clicks": True,
                "filters": [],
                "offers": [
                    {
                        "offer_id": payload.offer_id,
                        "share": 100,
                        "state": "active",
                    }
                ],
            }
        )
        stream_ids.append(int(offers_stream["id"]))

        local = CampaignLocal(
            keitaro_campaign_id=campaign_id,
            name=name,
            alias=alias,
            geo=geo,
            domain_id=domain_id,
            group_id=group_id,
            traffic_source_id=source_id,
        )
        local.streams = [
            StreamLocal(
                keitaro_stream_id=int(google_stream["id"]),
                name=google_stream.get("name") or f"Geo {geo} → Google",
                stream_type="regular",
                stream_schema="redirect",
                has_offers=False,
                is_dirty=False,
            ),
            StreamLocal(
                keitaro_stream_id=int(offers_stream["id"]),
                name=offers_stream.get("name") or "Offers",
                stream_type="default",
                stream_schema="landings",
                has_offers=True,
                is_dirty=False,
                offers=[
                    StreamOfferLocal(
                        keitaro_offer_id=payload.offer_id,
                        offer_name=offer_name,
                        share=100,
                        pinned=False,
                        status="active",
                    )
                ],
            ),
        ]
        db.add(local)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Create campaign %r failed: %s: %s", name, type(exc).__name__, exc)
        if campaign_id is not None:
            _compensate_create(client, campaign_id, stream_ids)
        if isinstance(exc, SQLAlchemyError):
            raise AppError(
                "Failed to save campaign locally; created Keitaro objects were rolled back",
                status_code=500,
            ) from exc
        raise

    db.refresh(local)
    logger.info(
        "Created campaign %s (%s) geo=%s offer=%s streams=%s",
        campaign_id,
        name,
        geo,
        payload.offer_id,
        stream_ids,
    )
    return {
        "campaign": campaign_to_detail(db, local),
        "keitaro_campaign": campaign,
        "streams": [google_stream, offers_stream],
    }


def list_local_campaigns(db: Session) -> list[CampaignSummary]:
    rows = db.query(CampaignLocal).order_by(CampaignLocal.id.desc()).all()
    return [
        CampaignSummary(
            id=r.id,
            keitaro_campaign_id=r.keitaro_campaign_id,
            name=r.name,
            alias=r.alias,
            geo=r.geo,
            domain_id=r.domain_id,
            group_id=r.group_id,
            traffic_source_id=r.traffic_source_id,
        )
        for r in rows
    ]


def get_local_by_keitaro_id(db: Session, keitaro_campaign_id: int) -> CampaignLocal | None:
    return (
        db.query(CampaignLocal)
        .options(joinedload(CampaignLocal.streams).joinedload(StreamLocal.offers))
        .filter(CampaignLocal.keitaro_campaign_id == keitaro_campaign_id)
        .first()
    )


def get_or_create_local_campaign(
    db: Session, client: KeitaroClient, keitaro_campaign_id: int
) -> CampaignLocal:
    local = get_local_by_keitaro_id(db, keitaro_campaign_id)
    if local:
        return local

    remote = client.get_campaign(keitaro_campaign_id)
    db.add(
        CampaignLocal(
            keitaro_campaign_id=keitaro_campaign_id,
            name=remote.get("name") or f"campaign_{keitaro_campaign_id}",
            alias=remote.get("alias"),
            domain_id=remote.get("domain_id"),
            group_id=remote.get("group_id"),
            traffic_source_id=remote.get("traffic_source_id"),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Another request inserted the same campaign concurrently; use its row.
        db.rollback()
        logger.info("Local campaign %s created concurrently; re-reading", keitaro_campaign_id)

    local = get_local_by_keitaro_id(db, keitaro_campaign_id)
    if local is None:
        raise AppError(f"Failed to create local mirror for campaign {keitaro_campaign_id}", status_code=500)
    return local


def campaign_to_detail(db: Session, local: CampaignLocal, keitaro: dict | None = None) -> CampaignDetail:
    # reload with relationships
    local = (
        db.query(CampaignLocal)
        .options(joinedload(CampaignLocal.streams).joinedload(StreamLocal.offers))
        .filter(CampaignLocal.id == local.id)
        .one()
    )
    streams: list[StreamView] = []
    for s in local.streams:
        offers = [
            StreamOfferView(
                keitaro_offer_id=o.keitaro_offer_id,
                offer_name=o.offer_name,
                share=o.share,
                pinned=o.pinned,
                status=o.status,
            )
            for o in s.offers
        ]
        streams.append(
            StreamView(
                id=s.id,
                keitaro_stream_id=s.keitaro_stream_id,
                name=s.name,
                stream_type=s.stream_type,
                stream_schema=s.stream_schema,
                has_offers=s.has_offers,
                is_dirty=s.is_dirty,
                offers=offers if s.has_offers else [],
            )
        )
    return CampaignDetail(
        id=local.id,
        keitaro_campaign_id=local.keitaro_campaign_id,
        name=local.name,
        alias=local.alias,
        geo=local.geo,
        domain_id=local.domain_id,
        group_id=local.group_id,
        traffic_source_id=local.traffic_source_id,
        streams=streams,
        keitaro=keitaro,
    )


def _offer_name(client: KeitaroClient, offer_id: int) -> str:
    try:
        return client.get_offer(offer_id).get("name") or str(offer_id)
    except KeitaroError:
        return str(offer_id)


def _remote_shares(remote_offers: list[dict]) -> dict[int, float]:
    return {
        int(o["offer_id"]): float(o.get("share") or 0)
        for o in remote_offers
        if o.get("offer_id") is not None
    }


def has_real_draft(stream: StreamLocal, remote_shares: dict[int, float]) -> bool:
    """
    A stream holds a real unpublished draft when the user edited it locally
    (`is_dirty`, set only by add/remove/bring-back/pin) AND the resulting active
    offer set or integer shares differ from what Keitaro currently has.
    A dirty flag whose content already equals Keitaro is not a draft.
    """
    if not stream.is_dirty:
        return False
    local = {o.keitaro_offer_id: int(round(o.share)) for o in stream.offers if o.status == "active"}
    remote = {oid: int(round(share)) for oid, share in remote_shares.items()}
    return local != remote


def _mirror_remote_offers(
    client: KeitaroClient,
    stream: StreamLocal,
    remote_offers: list[dict],
    *,
    drop_missing: bool,
    clear_pins: bool,
) -> None:
    """
    Make local offers mirror Keitaro and clear the dirty flag.

    Offers present remotely become active with the remote share. Local offers absent
    remotely are deleted (`drop_missing`) or kept as `removed` archive rows at 0%.
    """
    remote_shares = _remote_shares(remote_offers)
    now = utcnow()
    for o in list(stream.offers):
        if o.keitaro_offer_id in remote_shares:
            o.status = "active"
            o.share = remote_shares[o.keitaro_offer_id]
            if clear_pins:
                o.pinned = False
        elif drop_missing:
            stream.offers.remove(o)
            continue
        else:
            o.status = "removed"
            o.share = 0
            o.pinned = False
        o.updated_at = now

    known = {o.keitaro_offer_id for o in stream.offers}
    for oid, share in remote_shares.items():
        if oid not in known:
            stream.offers.append(
                StreamOfferLocal(
                    keitaro_offer_id=oid,
                    offer_name=_offer_name(client, oid),
                    share=share,
                    pinned=False,
                    status="active",
                )
            )
    stream.is_dirty = False
    stream.updated_at = now


def fetch_streams(
    db: Session, client: KeitaroClient, keitaro_campaign_id: int
) -> CampaignDetail:
    """
    Pull streams from Keitaro into the local DB.

    - Local streams missing in Keitaro are deleted locally.
    - New remote streams are added; metadata of existing ones is refreshed.
    - Offers mirror Keitaro (shares synced, offers gone remotely archived as
      `removed`, is_dirty=False) unless the stream holds a real draft
      (see `has_real_draft`), in which case its local offers are left untouched.
    """
    local = get_or_create_local_campaign(db, client, keitaro_campaign_id)
    remote_streams = client.list_campaign_streams(keitaro_campaign_id)
    remote_by_id = {int(s["id"]): s for s in remote_streams}

    existing = {s.keitaro_stream_id: s for s in local.streams}

    removed_ids = [sid for sid in existing if sid not in remote_by_id]
    for sid in removed_ids:
        stream = existing.pop(sid)
        if stream.is_dirty:
            logger.warning(
                "Stream %s deleted in Keitaro; discarding its unpublished local draft", sid
            )
        local.streams.remove(stream)

    kept_drafts: list[int] = []
    for sid, remote in remote_by_id.items():
        offers = remote.get("offers") or []
        stream = existing.get(sid)
        if stream is None:
            stream = StreamLocal(
                keitaro_stream_id=sid,
                name=remote.get("name") or f"Stream {sid}",
                stream_type=remote.get("type") or "regular",
                stream_schema=remote.get("schema") or "",
                has_offers=bool(offers),
                is_dirty=False,
            )
            local.streams.append(stream)
        else:
            stream.name = remote.get("name") or stream.name
            stream.stream_type = remote.get("type") or stream.stream_type
            stream.stream_schema = remote.get("schema") or stream.stream_schema

        if has_real_draft(stream, _remote_shares(offers)):
            kept_drafts.append(sid)
            continue

        stream.has_offers = bool(offers)
        _mirror_remote_offers(client, stream, offers, drop_missing=False, clear_pins=False)

    db.commit()
    logger.info(
        "Fetched campaign %s: %d streams, removed locally %s, kept drafts %s",
        keitaro_campaign_id,
        len(remote_by_id),
        removed_ids,
        kept_drafts,
    )
    return campaign_to_detail(db, local, keitaro=client.get_campaign(keitaro_campaign_id))


def _get_stream(db: Session, stream_local_id: int) -> StreamLocal:
    stream = (
        db.query(StreamLocal)
        .options(joinedload(StreamLocal.offers))
        .execution_options(populate_existing=True)
        .filter(StreamLocal.id == stream_local_id)
        .first()
    )
    if not stream:
        raise AppError(f"Local stream {stream_local_id} not found", status_code=404)
    if not stream.has_offers:
        raise AppError("Cannot manage offers on a stream without offers", status_code=400)
    return stream


def _apply_shares(stream: StreamLocal) -> None:
    payload = [
        {
            "id": o.id,
            "share": o.share,
            "pinned": o.pinned,
            "status": o.status,
        }
        for o in stream.offers
    ]
    redistribute(payload)
    by_id = {p["id"]: p for p in payload}
    now = utcnow()
    for o in stream.offers:
        o.share = float(by_id[o.id]["share"])
        o.updated_at = now
    stream.is_dirty = True
    stream.updated_at = now


def add_offer(
    db: Session, client: KeitaroClient, stream_local_id: int, payload: AddOfferRequest
) -> CampaignDetail:
    stream = _get_stream(db, stream_local_id)
    campaign_local_id = stream.campaign_id
    offer_id = payload.offer_id
    existing = next((o for o in stream.offers if o.keitaro_offer_id == offer_id), None)
    if existing:
        if existing.status == "active":
            raise AppError(f"Offer {offer_id} already in stream", status_code=400)
        existing.status = "active"
        existing.offer_name = _offer_name(client, offer_id) or existing.offer_name
    else:
        db.add(
            StreamOfferLocal(
                stream_id=stream.id,
                keitaro_offer_id=offer_id,
                offer_name=_offer_name(client, offer_id),
                share=0,
                pinned=False,
                status="active",
            )
        )
        db.flush()

    stream = _get_stream(db, stream_local_id)
    _apply_shares(stream)
    db.commit()
    campaign = db.query(CampaignLocal).filter(CampaignLocal.id == campaign_local_id).one()
    return campaign_to_detail(db, campaign)


def remove_offer(db: Session, stream_local_id: int, offer_id: int) -> CampaignDetail:
    stream = _get_stream(db, stream_local_id)
    offer = next((o for o in stream.offers if o.keitaro_offer_id == offer_id), None)
    if not offer:
        raise AppError(f"Offer {offer_id} not found in stream", status_code=404)
    offer.status = "removed"
    offer.share = 0
    offer.pinned = False
    _apply_shares(stream)
    db.commit()
    return campaign_to_detail(db, stream.campaign)


def bring_back_offer(db: Session, stream_local_id: int, offer_id: int) -> CampaignDetail:
    stream = _get_stream(db, stream_local_id)
    offer = next((o for o in stream.offers if o.keitaro_offer_id == offer_id), None)
    if not offer:
        raise AppError(f"Offer {offer_id} not found in stream", status_code=404)
    offer.status = "active"
    _apply_shares(stream)
    db.commit()
    return campaign_to_detail(db, stream.campaign)


def pin_offer(
    db: Session, stream_local_id: int, offer_id: int, payload: PinOfferRequest
) -> CampaignDetail:
    stream = _get_stream(db, stream_local_id)
    offer = next((o for o in stream.offers if o.keitaro_offer_id == offer_id), None)
    if not offer or offer.status != "active":
        raise AppError(f"Active offer {offer_id} not found", status_code=404)
    offer.pinned = payload.pinned
    if payload.share is not None:
        offer.share = payload.share
    _apply_shares(stream)
    db.commit()
    return campaign_to_detail(db, stream.campaign)


def cancel_stream(db: Session, client: KeitaroClient, stream_local_id: int) -> CampaignDetail:
    """Discard unpublished local changes — re-sync from Keitaro for this stream."""
    stream = _get_stream(db, stream_local_id)
    campaign_local_id = stream.campaign_id
    remote = client.get_stream(stream.keitaro_stream_id)
    _mirror_remote_offers(
        client, stream, remote.get("offers") or [], drop_missing=True, clear_pins=True
    )
    db.commit()
    logger.info("Cancelled local draft of stream %s", stream.keitaro_stream_id)
    campaign = db.query(CampaignLocal).filter(CampaignLocal.id == campaign_local_id).one()
    return campaign_to_detail(db, campaign)


def push_stream(db: Session, client: KeitaroClient, stream_local_id: int) -> CampaignDetail:
    """Push local offer list/weights to Keitaro (integer shares summing to exactly 100)."""
    stream = _get_stream(db, stream_local_id)
    active = [o for o in stream.offers if o.status == "active"]
    shares = normalize_to_100([o.share for o in active])
    body = {
        "offers": [
            {
                "offer_id": o.keitaro_offer_id,
                "share": share,
                "state": "active",
            }
            for o, share in zip(active, shares)
        ]
    }
    client.update_stream(stream.keitaro_stream_id, body)
    now = utcnow()
    for o, share in zip(active, shares):
        o.share = share
        o.updated_at = now
    stream.is_dirty = False
    stream.updated_at = now
    db.commit()
    logger.info(
        "Pushed stream %s: %s",
        stream.keitaro_stream_id,
        {o["offer_id"]: o["share"] for o in body["offers"]},
    )
    return campaign_to_detail(db, stream.campaign)
