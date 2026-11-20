import type {
  AddOfferRequest,
  CampaignDetail,
  CampaignSummary,
  CreateCampaignRequest,
  CreatedCampaignResponse,
  DomainOut,
  GroupOut,
  HealthResponse,
  OfferOut,
  PinOfferRequest,
  TrafficSourceOut,
  ValidationErrorItem,
} from './types'

// ---- Auth (optional API token) ----
// Everything auth-related lives here: change AUTH_HEADER_NAME / formatAuthHeaderValue
// if the backend expects e.g. `X-API-Key: <token>` instead of a Bearer token.

const AUTH_HEADER_NAME = 'Authorization'
const formatAuthHeaderValue = (token: string): string => `Bearer ${token}`

const TOKEN_STORAGE_KEY = 'adrobot.apiToken'
const ENV_TOKEN = (import.meta.env.VITE_ADROBOT_API_TOKEN ?? '').trim()

export function getStoredApiToken(): string {
  try {
    return (localStorage.getItem(TOKEN_STORAGE_KEY) ?? '').trim()
  } catch {
    return ''
  }
}

export function setStoredApiToken(token: string): void {
  try {
    const value = token.trim()
    if (value) localStorage.setItem(TOKEN_STORAGE_KEY, value)
    else localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // localStorage unavailable (private mode / disabled) — token falls back to env.
  }
}

export function hasEnvApiToken(): boolean {
  return ENV_TOKEN !== ''
}

/** Token saved in the UI wins over the build-time VITE_ADROBOT_API_TOKEN. */
export function getApiToken(): string {
  return getStoredApiToken() || ENV_TOKEN
}

function authHeaders(): Record<string, string> {
  const token = getApiToken()
  return token ? { [AUTH_HEADER_NAME]: formatAuthHeaderValue(token) } : {}
}

// ---- Errors ----

export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached the server. */
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(formatErrorMessage(status, detail))
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function statusLabel(status: number): string {
  switch (status) {
    case 0:
      return 'Ошибка сети'
    case 400:
      return 'Некорректный запрос'
    case 401:
    case 403:
      return 'Нет доступа'
    case 404:
      return 'Не найдено'
    case 422:
      return 'Ошибка проверки данных'
    case 500:
      return 'Ошибка сервера'
    case 502:
      return 'Ошибка на стороне Keitaro'
    case 503:
      return 'Сервис недоступен'
    case 504:
      return 'Keitaro не ответил вовремя'
    default:
      return 'Ошибка запроса'
  }
}

function formatErrorMessage(status: number, detail: string): string {
  const label = status ? `${statusLabel(status)} (HTTP ${status})` : statusLabel(status)
  const hint =
    status === 401 || status === 403
      ? ' — укажите действующий API-токен в «Настройках» в шапке страницы.'
      : ''
  return `${label}: ${detail}${hint}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isValidationItem(value: unknown): value is ValidationErrorItem {
  return isRecord(value) && typeof value.msg === 'string' && Array.isArray(value.loc)
}

function stringifyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        isValidationItem(item)
          ? `${item.loc.filter((part) => part !== 'body').join('.') || 'запрос'}: ${item.msg}`
          : JSON.stringify(item),
      )
      .join('; ')
  }
  return JSON.stringify(detail)
}

function extractDetail(body: unknown, rawText: string, statusText: string): string {
  if (isRecord(body) && body.detail !== undefined && body.detail !== null) {
    return stringifyDetail(body.detail)
  }
  const text = rawText.trim()
  if (text) return text.length > 300 ? `${text.slice(0, 300)}…` : text
  return statusText || 'подробности отсутствуют'
}

/** Human-readable message for anything thrown by an API call. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

// ---- Transport ----

type HttpMethod = 'GET' | 'POST' | 'DELETE'

async function request<T>(method: HttpMethod, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...authHeaders() }
  const init: RequestInit = { method, headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  let res: Response
  try {
    res = await fetch(path, init)
  } catch (err) {
    throw new ApiError(0, `не удаётся связаться с API (${errorMessage(err)})`)
  }

  const text = await res.text()
  let data: unknown = null
  let parsed = true
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      parsed = false
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, extractDetail(parsed ? data : null, text, res.statusText))
  }
  if (!parsed) {
    throw new ApiError(res.status, 'ответ сервера не является корректным JSON')
  }
  return data as T
}

const enc = encodeURIComponent

// ---- Endpoints (app/main.py) ----

export const api = {
  health: () => request<HealthResponse>('GET', '/api/health'),

  listDomains: () => request<DomainOut[]>('GET', '/api/meta/domains'),
  listGroups: () => request<GroupOut[]>('GET', '/api/meta/groups'),
  listTrafficSources: () => request<TrafficSourceOut[]>('GET', '/api/meta/traffic-sources'),

  searchOffers: (query: string, limit = 80) => {
    const q = query.trim()
    return request<OfferOut[]>('GET', `/api/offers?limit=${limit}${q ? `&q=${enc(q)}` : ''}`)
  },

  createCampaign: (body: CreateCampaignRequest) =>
    request<CreatedCampaignResponse>('POST', '/api/campaigns', body),
  listLocalCampaigns: () => request<CampaignSummary[]>('GET', '/api/campaigns'),
  getCampaign: (keitaroCampaignId: number) =>
    request<CampaignDetail>('GET', `/api/campaigns/${keitaroCampaignId}`),
  fetchStreams: (keitaroCampaignId: number) =>
    request<CampaignDetail>('POST', `/api/campaigns/${keitaroCampaignId}/fetch-streams`),

  addOffer: (streamId: number, body: AddOfferRequest) =>
    request<CampaignDetail>('POST', `/api/streams/${streamId}/offers`, body),
  removeOffer: (streamId: number, offerId: number) =>
    request<CampaignDetail>('DELETE', `/api/streams/${streamId}/offers/${offerId}`),
  bringBackOffer: (streamId: number, offerId: number) =>
    request<CampaignDetail>('POST', `/api/streams/${streamId}/offers/${offerId}/bring-back`),
  pinOffer: (streamId: number, offerId: number, body: PinOfferRequest) =>
    request<CampaignDetail>('POST', `/api/streams/${streamId}/offers/${offerId}/pin`, body),
  pushStream: (streamId: number) =>
    request<CampaignDetail>('POST', `/api/streams/${streamId}/push`),
  cancelStream: (streamId: number) =>
    request<CampaignDetail>('POST', `/api/streams/${streamId}/cancel`),
}
