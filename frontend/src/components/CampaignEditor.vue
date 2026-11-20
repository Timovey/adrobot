<script setup lang="ts">
import { computed, reactive, ref, shallowRef } from 'vue'
import { api, errorMessage } from '../api'
import type {
  CampaignDetail,
  CampaignSummary,
  StreamAction,
  StreamOfferView,
  StreamView,
  ToastPayload,
} from '../types'
import StreamCard from './StreamCard.vue'
import OfferPicker from './OfferPicker.vue'

const campaignId = defineModel<string>('campaignId', { default: '' })
const emit = defineEmits<{ toast: [payload: ToastPayload] }>()

// Owned locally (not a v-model) so concurrent per-stream responses always merge into the latest state.
const campaign = shallowRef<CampaignDetail | null>(null)
const localList = ref<CampaignSummary[]>([])
const showLocalList = ref(false)
const targetStreamId = ref<number | null>(null)
const lastFetchedAt = ref<Date | null>(null)

// Busy state is per action: campaign-level loads and one in-flight action per stream.
const fetching = ref(false)
const loadingLocal = ref(false)
const fetchError = ref('')
const pending = reactive(new Map<number, StreamAction>())
const streamErrors = reactive(new Map<number, string>())

const streams = computed<StreamView[]>(() => campaign.value?.streams ?? [])

const targetStream = computed<StreamView | null>(
  () => streams.value.find((s) => s.id === targetStreamId.value) ?? null,
)

const targetStreamLabel = computed(() => {
  const s = targetStream.value
  return s ? `${s.name} (#${s.id})` : '—'
})

const dirtyCount = computed(() => streams.value.filter((s) => s.is_dirty).length)

/** Russian plural form: 1 поток, 2 потока, 5 потоков. */
function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

const dirtyLabel = computed(
  () =>
    `● ${dirtyCount.value} ${pluralRu(dirtyCount.value, 'поток', 'потока', 'потоков')} ` +
    'с неопубликованными изменениями',
)
const hasPendingStreamActions = computed(() => streams.value.some((s) => pending.has(s.id)))

function isStreamLocked(streamId: number): boolean {
  return fetching.value || pending.has(streamId)
}

function toastError(err: unknown): string {
  const message = errorMessage(err)
  emit('toast', { message, type: 'error' })
  return message
}

function onCampaignIdInput(event: Event): void {
  campaignId.value = (event.target as HTMLInputElement).value
}

async function fetchStreams(id?: number): Promise<void> {
  if (fetching.value) return
  const resolvedId = id ?? Number(campaignId.value)
  if (!Number.isInteger(resolvedId) || resolvedId <= 0) {
    emit('toast', { message: 'Введите ID кампании', type: 'error' })
    return
  }
  fetching.value = true
  fetchError.value = ''
  try {
    const data = await api.fetchStreams(resolvedId)
    // Full replace: the server response is the source of truth for every stream.
    campaign.value = data
    campaignId.value = String(data.keitaro_campaign_id)
    streamErrors.clear()
    if (!data.streams.some((s) => s.id === targetStreamId.value)) targetStreamId.value = null
    lastFetchedAt.value = new Date()
    emit('toast', { message: 'Потоки загружены', type: 'ok' })
  } catch (err) {
    fetchError.value = toastError(err)
  } finally {
    fetching.value = false
  }
}

async function loadLocal(): Promise<void> {
  if (loadingLocal.value) return
  loadingLocal.value = true
  try {
    localList.value = await api.listLocalCampaigns()
    showLocalList.value = true
  } catch (err) {
    toastError(err)
  } finally {
    loadingLocal.value = false
  }
}

function selectLocal(c: CampaignSummary): void {
  campaignId.value = String(c.keitaro_campaign_id)
  void fetchStreams(c.keitaro_campaign_id)
}

/** Replace only `streamId` from a stream-scoped response; other streams keep their own latest state. */
function mergeStream(streamId: number, data: CampaignDetail): void {
  const current = campaign.value
  if (!current || current.id !== data.id) return // user switched campaign meanwhile
  const updated = data.streams.find((s) => s.id === streamId)
  if (!updated || !current.streams.some((s) => s.id === streamId)) {
    campaign.value = { ...data, keitaro: current.keitaro }
    return
  }
  campaign.value = {
    ...current,
    streams: current.streams.map((s) => (s.id === streamId ? updated : s)),
  }
}

async function runStreamAction(
  stream: StreamView,
  action: StreamAction,
  call: () => Promise<CampaignDetail>,
  okMessage?: string,
): Promise<void> {
  if (isStreamLocked(stream.id)) return
  pending.set(stream.id, action)
  streamErrors.delete(stream.id)
  try {
    mergeStream(stream.id, await call())
    if (okMessage) emit('toast', { message: okMessage, type: 'ok' })
  } catch (err) {
    streamErrors.set(stream.id, toastError(err))
  } finally {
    pending.delete(stream.id)
  }
}

function removeOffer(stream: StreamView, offer: StreamOfferView): void {
  const offerId = offer.keitaro_offer_id
  void runStreamAction(
    stream,
    { kind: 'remove', offerId },
    () => api.removeOffer(stream.id, offerId),
    `Оффер ${offerId} удалён`,
  )
}

function bringBackOffer(stream: StreamView, offer: StreamOfferView): void {
  const offerId = offer.keitaro_offer_id
  void runStreamAction(
    stream,
    { kind: 'bring-back', offerId },
    () => api.bringBackOffer(stream.id, offerId),
    `Оффер ${offerId} возвращён`,
  )
}

function pinOffer(stream: StreamView, offer: StreamOfferView): void {
  const offerId = offer.keitaro_offer_id
  const share = offer.pinned ? undefined : Number(offer.share)
  void runStreamAction(stream, { kind: 'pin', offerId }, () =>
    api.pinOffer(stream.id, offerId, { pinned: !offer.pinned, share }),
  )
}

function pushStream(stream: StreamView): void {
  void runStreamAction(
    stream,
    { kind: 'push' },
    () => api.pushStream(stream.id),
    `Поток «${stream.name}» опубликован в Keitaro`,
  )
}

function cancelStream(stream: StreamView): void {
  void runStreamAction(
    stream,
    { kind: 'cancel' },
    () => api.cancelStream(stream.id),
    `Черновик потока «${stream.name}» отменён`,
  )
}

function openPicker(stream: StreamView): void {
  targetStreamId.value = stream.id
}

function addOffer(offerId: number): void {
  const stream = targetStream.value
  if (!stream) {
    emit('toast', { message: 'Сначала выберите поток', type: 'error' })
    return
  }
  void runStreamAction(
    stream,
    { kind: 'add' },
    () => api.addOffer(stream.id, { offer_id: offerId }),
    `Оффер ${offerId} добавлен`,
  )
}

defineExpose({ fetchStreams })
</script>

<template>
  <section class="panel" id="editor">
    <h2>2. Редактор кампании</h2>
    <div class="form-row">
      <label class="field">
        ID кампании в Keitaro
        <input
          :value="campaignId"
          type="number"
          min="1"
          placeholder="213039"
          @input="onCampaignIdInput"
          @keydown.enter.prevent="fetchStreams()"
        />
      </label>
      <div class="btn-group">
        <button
          type="button"
          :disabled="fetching || hasPendingStreamActions"
          :title="hasPendingStreamActions ? 'Дождитесь завершения действий с потоками' : ''"
          @click="fetchStreams()"
        >
          {{ fetching ? 'Загрузка…' : 'Загрузить потоки' }}
        </button>
        <button
          type="button"
          class="secondary"
          :disabled="loadingLocal"
          title="Кампании, сохранённые в локальной базе"
          @click="loadLocal"
        >
          {{ loadingLocal ? 'Загрузка…' : 'Сохранённые' }}
        </button>
      </div>
    </div>

    <div v-if="showLocalList" class="local-list">
      <template v-if="localList.length">
        <button
          v-for="c in localList"
          :key="c.id"
          type="button"
          class="secondary"
          :disabled="fetching"
          @click="selectLocal(c)"
        >
          {{ c.keitaro_campaign_id }}: {{ c.name }}
        </button>
      </template>
      <span v-else class="hint">Сохранённых кампаний пока нет.</span>
    </div>

    <div v-if="fetchError" class="error-box" role="alert">
      <span>{{ fetchError }}</span>
      <button
        type="button"
        class="secondary small icon"
        aria-label="Скрыть ошибку"
        @click="fetchError = ''"
      >
        ×
      </button>
    </div>

    <div v-if="campaign" class="meta">
      <div>
        <strong>{{ campaign.name }}</strong>
        · Keitaro #{{ campaign.keitaro_campaign_id }}
        <template v-if="campaign.geo"> · гео {{ campaign.geo }}</template>
        · домен {{ campaign.domain_id || '—' }}
        · группа {{ campaign.group_id || '—' }}
        · источник {{ campaign.traffic_source_id || '—' }}
      </div>
      <div class="meta-status">
        <span v-if="dirtyCount" class="badge warn">{{ dirtyLabel }}</span>
        <span v-else class="badge ok">✓ Все потоки синхронизированы с Keitaro</span>
        <span v-if="lastFetchedAt" class="hint">
          обновлено в {{ lastFetchedAt.toLocaleTimeString('ru-RU') }}
        </span>
      </div>
    </div>

    <div v-if="campaign" :class="{ reloading: fetching }">
      <StreamCard
        v-for="stream in campaign.streams"
        :key="stream.id"
        :stream="stream"
        :disabled="isStreamLocked(stream.id)"
        :pending="pending.get(stream.id) ?? null"
        :error="streamErrors.get(stream.id) ?? ''"
        :picker-target="stream.id === targetStreamId"
        @add-offer="openPicker"
        @remove="(offer) => removeOffer(stream, offer)"
        @bring-back="(offer) => bringBackOffer(stream, offer)"
        @pin="(offer) => pinOffer(stream, offer)"
        @push="pushStream(stream)"
        @cancel="cancelStream(stream)"
        @dismiss-error="streamErrors.delete(stream.id)"
      />
    </div>

    <OfferPicker
      v-if="targetStream"
      :target-label="targetStreamLabel"
      :disabled="isStreamLocked(targetStream.id)"
      :adding="pending.get(targetStream.id)?.kind === 'add'"
      @toast="(payload) => emit('toast', payload)"
      @add="addOffer"
      @close="targetStreamId = null"
    />
  </section>
</template>
