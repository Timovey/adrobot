from pydantic import BaseModel, Field


class CreateCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Campaign name (prefix applied if missing)")
    geo: str = Field(..., min_length=2, max_length=2, description="ISO country code, e.g. AU")
    offer_id: int = Field(..., gt=0)
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class OfferOut(BaseModel):
    id: int
    name: str
    state: str | None = None
    group_id: int | None = None


class DomainOut(BaseModel):
    id: int
    name: str
    state: str | None = None


class GroupOut(BaseModel):
    id: int
    name: str
    type: str | None = None


class TrafficSourceOut(BaseModel):
    id: int
    name: str
    state: str | None = None


class CampaignSummary(BaseModel):
    id: int
    keitaro_campaign_id: int
    name: str
    alias: str | None = None
    geo: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class StreamOfferView(BaseModel):
    keitaro_offer_id: int
    offer_name: str = ""
    share: float
    pinned: bool = False
    status: str = "active"  # active | removed


class StreamView(BaseModel):
    id: int
    keitaro_stream_id: int
    name: str
    stream_type: str
    stream_schema: str
    has_offers: bool
    is_dirty: bool
    offers: list[StreamOfferView] = []


class CampaignDetail(BaseModel):
    id: int
    keitaro_campaign_id: int
    name: str
    alias: str | None = None
    geo: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None
    streams: list[StreamView] = []
    keitaro: dict | None = None


class AddOfferRequest(BaseModel):
    offer_id: int = Field(..., gt=0)


class PinOfferRequest(BaseModel):
    pinned: bool = True
    share: float | None = Field(default=None, ge=0, le=100)


class CreatedCampaignResponse(BaseModel):
    campaign: CampaignDetail
    keitaro_campaign: dict
    streams: list[dict]
