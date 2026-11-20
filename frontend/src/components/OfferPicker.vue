<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, errorMessage } from '../api'
import type { OfferOut, ToastPayload } from '../types'

withDefaults(
  defineProps<{
    targetLabel?: string
    /** Target stream is busy — adding is blocked, searching is not. */
    disabled?: boolean
    adding?: boolean
  }>(),
  { targetLabel: '—', disabled: false, adding: false },
)

const emit = defineEmits<{
  add: [offerId: number]
  close: []
  toast: [payload: ToastPayload]
}>()

const query = ref('')
const offers = ref<OfferOut[]>([])
const selectedOfferId = ref('')
const searching = ref(false)
const searchError = ref('')

async function searchOffers(): Promise<void> {
  if (searching.value) return
  searching.value = true
  searchError.value = ''
  try {
    const items = await api.searchOffers(query.value)
    offers.value = items
    selectedOfferId.value = items[0] ? String(items[0].id) : ''
  } catch (err) {
    searchError.value = errorMessage(err)
    emit('toast', { message: searchError.value, type: 'error' })
  } finally {
    searching.value = false
  }
}

function onAdd(): void {
  const offerId = Number(selectedOfferId.value)
  if (!offerId) {
    emit('toast', { message: 'Выберите оффер', type: 'error' })
    return
  }
  emit('add', offerId)
}

onMounted(searchOffers)
</script>

<template>
  <div class="offer-picker panel-inner">
    <div class="picker-head">
      <h3>Добавить оффер</h3>
      <button type="button" class="secondary small" @click="emit('close')">Закрыть</button>
    </div>
    <div class="form-row">
      <label class="field">
        Поиск оффера
        <input
          v-model="query"
          placeholder="ID или название"
          @keydown.enter.prevent="searchOffers"
        />
      </label>
      <div class="btn-group">
        <button type="button" class="secondary" :disabled="searching" @click="searchOffers">
          {{ searching ? 'Поиск…' : 'Найти' }}
        </button>
      </div>
    </div>
    <div v-if="searchError" class="error-box" role="alert">{{ searchError }}</div>
    <select
      v-model="selectedOfferId"
      class="offer-select"
      size="6"
      aria-label="Найденные офферы"
    >
      <option v-for="o in offers" :key="o.id" :value="String(o.id)">
        {{ o.id }} — {{ o.name }}
      </option>
    </select>
    <p v-if="!searching && !offers.length && !searchError" class="hint">Ничего не найдено.</p>
    <div class="form-row center">
      <button type="button" :disabled="disabled || !selectedOfferId" @click="onAdd">
        {{ adding ? 'Добавление…' : 'Добавить выбранный оффер' }}
      </button>
      <span class="hint">Целевой поток: {{ targetLabel }}</span>
    </div>
  </div>
</template>
