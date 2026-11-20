<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, errorMessage } from '../api'
import type {
  CreateCampaignRequest,
  CreatedCampaignResponse,
  DomainOut,
  GroupOut,
  NamedEntity,
  ToastPayload,
  TrafficSourceOut,
} from '../types'

const emit = defineEmits<{
  created: [data: CreatedCampaignResponse]
  toast: [payload: ToastPayload]
}>()

const form = reactive<{
  name: string
  geo: string
  /** `<input type="number">` with v-model yields a number once parseable. */
  offer_id: string | number
  domain_id: string
  group_id: string
  traffic_source_id: string
}>({
  name: '',
  geo: '',
  offer_id: '',
  domain_id: '',
  group_id: '',
  traffic_source_id: '',
})

const domains = ref<DomainOut[]>([])
const groups = ref<GroupOut[]>([])
const sources = ref<TrafficSourceOut[]>([])
const createResult = ref<CreatedCampaignResponse | null>(null)
const submitting = ref(false)
const loadingMeta = ref(false)
const metaError = ref('')
const submitError = ref('')

function preferByName(items: NamedEntity[], name: string): string {
  const match = items.find((item) => String(item.name).toUpperCase().includes(name))
  const pick = match ?? items[0]
  return pick ? String(pick.id) : ''
}

async function loadMeta(): Promise<void> {
  loadingMeta.value = true
  metaError.value = ''
  try {
    const [domainItems, groupItems, sourceItems] = await Promise.all([
      api.listDomains(),
      api.listGroups(),
      api.listTrafficSources(),
    ])
    domains.value = domainItems
    groups.value = groupItems
    sources.value = sourceItems

    form.domain_id = domainItems[0] ? String(domainItems[0].id) : ''
    form.group_id = preferByName(groupItems, 'FORTESTS')
    form.traffic_source_id = preferByName(sourceItems, 'FORTESTS')
  } catch (err) {
    metaError.value = errorMessage(err)
    emit('toast', { message: metaError.value, type: 'error' })
  } finally {
    loadingMeta.value = false
  }
}

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  submitError.value = ''
  createResult.value = null
  try {
    const body: CreateCampaignRequest = {
      name: form.name,
      geo: String(form.geo || '').toUpperCase(),
      offer_id: Number(form.offer_id),
      domain_id: Number(form.domain_id),
      group_id: Number(form.group_id),
      traffic_source_id: Number(form.traffic_source_id),
    }
    const data = await api.createCampaign(body)
    createResult.value = data
    emit('created', data)
  } catch (err) {
    submitError.value = errorMessage(err)
    emit('toast', { message: submitError.value, type: 'error' })
  } finally {
    submitting.value = false
  }
}

onMounted(loadMeta)
</script>

<template>
  <section class="panel" id="creator">
    <h2>1. Создание кампании</h2>
    <div v-if="metaError" class="error-box" role="alert">
      <span>Не удалось загрузить домены, группы и источники. {{ metaError }}</span>
      <button type="button" class="secondary small" :disabled="loadingMeta" @click="loadMeta">
        {{ loadingMeta ? 'Повтор…' : 'Повторить' }}
      </button>
    </div>
    <form @submit.prevent="onSubmit">
      <label class="field">
        Название
        <input v-model="form.name" required placeholder="campaign1" />
      </label>
      <div class="form-row">
        <label class="field">
          Гео (ISO-код страны)
          <input
            v-model="form.geo"
            class="geo-input"
            required
            maxlength="2"
            placeholder="AU"
          />
        </label>
        <label class="field">
          ID оффера
          <input v-model="form.offer_id" type="number" required min="1" placeholder="25327" />
        </label>
      </div>
      <label class="field">
        Домен
        <select v-model="form.domain_id" required>
          <option v-for="d in domains" :key="d.id" :value="String(d.id)">
            {{ d.id }} — {{ d.name }}
          </option>
        </select>
      </label>
      <label class="field">
        Группа
        <select v-model="form.group_id" required>
          <option v-for="g in groups" :key="g.id" :value="String(g.id)">
            {{ g.id }} — {{ g.name }}
          </option>
        </select>
      </label>
      <label class="field">
        Источник трафика
        <select v-model="form.traffic_source_id" required>
          <option v-for="s in sources" :key="s.id" :value="String(s.id)">
            {{ s.id }} — {{ s.name }}
          </option>
        </select>
      </label>
      <button type="submit" :disabled="submitting">
        {{ submitting ? 'Создание…' : 'Создать кампанию' }}
      </button>
    </form>
    <div v-if="submitError" class="error-box" role="alert">{{ submitError }}</div>
    <pre v-if="createResult" class="result">{{ JSON.stringify(createResult, null, 2) }}</pre>
  </section>
</template>
