// Mirrors the Pydantic schemas in app/schemas.py and the ad-hoc dicts returned by app/main.py.

export type JsonObject = { [key: string]: unknown }

// ---- Requests ----

export interface CreateCampaignRequest {
  name: string
  /** ISO country code, exactly 2 chars. */
  geo: string
  offer_id: number
  domain_id?: number | null
  group_id?: number | null
  traffic_source_id?: number | null
}

export interface AddOfferRequest {
  offer_id: number
}

export interface PinOfferRequest {
  pinned: boolean
  /** 0..100; omitted when unpinning. */
  share?: number | null
}

// ---- Responses ----

export interface HealthResponse {
  ok: boolean
  keitaro_url: string
  prefix: string
}

export interface OfferOut {
  id: number
  name: string
  state: string | null
  group_id: number | null
}

export interface DomainOut {
  id: number
  name: string
  state: string | null
}

export interface GroupOut {
  id: number
  name: string
  type: string | null
}

export interface TrafficSourceOut {
  id: number
  name: string
  state: string | null
}

/** Anything with id + name, used by the creator's select boxes. */
export type NamedEntity = DomainOut | GroupOut | TrafficSourceOut

export interface CampaignSummary {
  id: number
  keitaro_campaign_id: number
  name: string
  alias: string | null
  geo: string | null
  domain_id: number | null
  group_id: number | null
  traffic_source_id: number | null
}

export type StreamOfferStatus = 'active' | 'removed'

export interface StreamOfferView {
  keitaro_offer_id: number
  offer_name: string
  share: number
  pinned: boolean
  status: StreamOfferStatus
}

export interface StreamView {
  /** Local DB id — used in /api/streams/{id}/... paths. */
  id: number
  keitaro_stream_id: number
  name: string
  stream_type: string
  stream_schema: string
  has_offers: boolean
  /** True when local offers/shares differ from Keitaro (unpublished draft). */
  is_dirty: boolean
  offers: StreamOfferView[]
}

export interface CampaignDetail extends CampaignSummary {
  streams: StreamView[]
  /** Raw Keitaro campaign payload (only on get / fetch-streams). */
  keitaro: JsonObject | null
}

export interface CreatedCampaignResponse {
  campaign: CampaignDetail
  keitaro_campaign: JsonObject
  streams: JsonObject[]
}

export interface KeitaroCampaignListItem {
  id: number | null
  name: string | null
  alias: string | null
  state: string | null
  domain_id: number | null
  group_id: number | null
  traffic_source_id: number | null
}

// ---- Errors ----

/** One item of FastAPI's 422 `detail` array. */
export interface ValidationErrorItem {
  loc: (string | number)[]
  msg: string
  type: string
}

/** FastAPI error body: `{"detail": "..."}` or `{"detail": [ValidationErrorItem, ...]}`. */
export interface ApiErrorBody {
  detail: string | ValidationErrorItem[] | JsonObject
}

// ---- UI ----

export type StreamActionKind = 'add' | 'remove' | 'bring-back' | 'pin' | 'push' | 'cancel'

/** An in-flight editor action on one stream (offerId set for per-offer actions). */
export interface StreamAction {
  kind: StreamActionKind
  offerId?: number
}

export type ToastType = 'ok' | 'error'

export interface ToastPayload {
  message: string
  type: ToastType
}
