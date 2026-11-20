<script setup lang="ts">
import { computed } from 'vue'
import type { StreamAction, StreamActionKind, StreamOfferView, StreamView } from '../types'

const props = withDefaults(
  defineProps<{
    stream: StreamView
    /** Locks every control of this stream (an action on it is running, or the campaign is reloading). */
    disabled?: boolean
    /** The action currently running on this stream, if any. */
    pending?: StreamAction | null
    /** Last API error for an action on this stream. */
    error?: string
    /** This stream is the OfferPicker's target. */
    pickerTarget?: boolean
  }>(),
  { disabled: false, pending: null, error: '', pickerTarget: false },
)

const emit = defineEmits<{
  addOffer: [stream: StreamView]
  remove: [offer: StreamOfferView]
  bringBack: [offer: StreamOfferView]
  pin: [offer: StreamOfferView]
  push: []
  cancel: []
  dismissError: []
}>()

function isPending(kind: StreamActionKind, offerId?: number): boolean {
  const p = props.pending
  return !!p && p.kind === kind && (offerId === undefined || p.offerId === offerId)
}

const activeTotal = computed(() =>
  props.stream.offers
    .filter((o) => o.status === 'active')
    .reduce((sum, o) => sum + Number(o.share), 0),
)
</script>

<template>
  <div class="stream" :class="{ dirty: stream.is_dirty, target: pickerTarget }">
    <div class="stream-head">
      <div>
        <div class="stream-title">{{ stream.name }}</div>
        <div class="hint">
          локальный #{{ stream.id }} · Keitaro #{{ stream.keitaro_stream_id }} ·
          {{ stream.stream_type }}/{{ stream.stream_schema }}
        </div>
      </div>
      <div class="badges">
        <span v-if="pending" class="badge busy">выполняется…</span>
        <span v-if="stream.is_dirty" class="badge warn">● Не опубликовано</span>
        <span v-else class="badge ok">✓ Синхронизировано с Keitaro</span>
      </div>
    </div>

    <div v-if="error" class="error-box" role="alert">
      <span>{{ error }}</span>
      <button
        type="button"
        class="secondary small icon"
        aria-label="Скрыть ошибку"
        @click="emit('dismissError')"
      >
        ×
      </button>
    </div>

    <p v-if="!stream.has_offers" class="hint">
      Поток без офферов — показан как обычный поток (фильтры и редирект не учитываются).
    </p>

    <template v-else>
      <div v-if="stream.is_dirty" class="dirty-note">
        Локальный черновик отличается от Keitaro. Нажмите <strong>«Опубликовать»</strong>, чтобы
        отправить офферы и веса в Keitaro, или <strong>«Отменить»</strong>, чтобы сбросить черновик
        и вернуть версию из Keitaro.
      </div>

      <div
        v-for="offer in stream.offers"
        :key="offer.keitaro_offer_id"
        class="offer"
        :class="{ removed: offer.status === 'removed' }"
      >
        <div>
          <div class="offer-id">
            #{{ offer.keitaro_offer_id }}{{ offer.pinned ? ' 📌' : '' }}
            <span v-if="offer.status === 'removed'" class="hint">(удалён)</span>
          </div>
          <div class="offer-name" :title="offer.offer_name">{{ offer.offer_name }}</div>
        </div>
        <div class="share">{{ Number(offer.share).toFixed(0) }}%</div>
        <div class="actions">
          <template v-if="offer.status === 'removed'">
            <button type="button" :disabled="disabled" @click="emit('bringBack', offer)">
              {{ isPending('bring-back', offer.keitaro_offer_id) ? 'Возврат…' : 'Вернуть' }}
            </button>
          </template>
          <template v-else>
            <button
              type="button"
              class="danger"
              :disabled="disabled"
              @click="emit('remove', offer)"
            >
              {{ isPending('remove', offer.keitaro_offer_id) ? 'Удаление…' : 'Удалить' }}
            </button>
            <button
              type="button"
              class="pin-btn"
              :disabled="disabled"
              @click="emit('pin', offer)"
            >
              {{
                isPending('pin', offer.keitaro_offer_id)
                  ? 'Сохранение…'
                  : offer.pinned
                    ? 'Открепить'
                    : 'Закрепить'
              }}
            </button>
          </template>
        </div>
      </div>

      <div class="stream-foot">
        <span class="hint">Сумма активных: {{ activeTotal.toFixed(0) }}%</span>
        <div class="actions">
          <button
            type="button"
            class="secondary"
            :disabled="disabled"
            @click="emit('addOffer', stream)"
          >
            {{ isPending('add') ? 'Добавление…' : 'Добавить оффер…' }}
          </button>
          <button
            type="button"
            :class="{ 'warn-btn': stream.is_dirty }"
            :disabled="disabled || !stream.is_dirty"
            :title="stream.is_dirty ? 'Отправить черновик в Keitaro' : 'Нет неопубликованных изменений'"
            @click="emit('push')"
          >
            {{ isPending('push') ? 'Публикация…' : 'Опубликовать' }}
          </button>
          <button
            type="button"
            class="secondary"
            :disabled="disabled || !stream.is_dirty"
            :title="
              stream.is_dirty
                ? 'Сбросить черновик и вернуть данные из Keitaro'
                : 'Нет неопубликованных изменений'
            "
            @click="emit('cancel')"
          >
            {{ isPending('cancel') ? 'Отмена…' : 'Отменить' }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
