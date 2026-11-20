<script setup lang="ts">
import { onMounted, reactive, ref, useTemplateRef } from 'vue'
import { api, errorMessage } from './api'
import type { CreatedCampaignResponse, ToastPayload, ToastType } from './types'
import ApiTokenSettings from './components/ApiTokenSettings.vue'
import CampaignCreator from './components/CampaignCreator.vue'
import CampaignEditor from './components/CampaignEditor.vue'

const OK_TOAST_MS = 3500

const toast = reactive<{ message: string; type: ToastType; visible: boolean }>({
  message: '',
  type: 'ok',
  visible: false,
})
let toastTimer: ReturnType<typeof setTimeout> | undefined

function showToast(message: string, type: ToastType = 'ok'): void {
  toast.message = message
  toast.type = type
  toast.visible = true
  clearTimeout(toastTimer)
  // Errors stay until dismissed so long upstream messages can actually be read.
  if (type === 'ok') {
    toastTimer = setTimeout(() => {
      toast.visible = false
    }, OK_TOAST_MS)
  }
}

function onToast({ message, type }: ToastPayload): void {
  showToast(message, type)
}

function dismissToast(): void {
  clearTimeout(toastTimer)
  toast.visible = false
}

const campaignId = ref('')
const editorRef = useTemplateRef<InstanceType<typeof CampaignEditor>>('editor')

async function onCampaignCreated(data: CreatedCampaignResponse): Promise<void> {
  const keitaroId = data.campaign.keitaro_campaign_id
  campaignId.value = String(keitaroId)
  showToast(`Кампания #${keitaroId} создана`)
  if (editorRef.value) {
    await editorRef.value.fetchStreams(keitaroId)
  }
}

onMounted(async () => {
  try {
    await api.health()
  } catch (err) {
    showToast(errorMessage(err), 'error')
  }
})
</script>

<template>
  <header class="top">
    <div>
      <div class="brand">Adrobot</div>
      <p class="tagline">Создание кампаний Keitaro и редактор офферов в потоках</p>
    </div>
    <ApiTokenSettings @toast="onToast" />
  </header>

  <main class="layout">
    <CampaignCreator @created="onCampaignCreated" @toast="onToast" />
    <CampaignEditor ref="editor" v-model:campaign-id="campaignId" @toast="onToast" />
  </main>

  <div
    v-if="toast.visible"
    class="toast"
    :class="toast.type"
    :role="toast.type === 'error' ? 'alert' : 'status'"
  >
    <span class="toast-message">{{ toast.message }}</span>
    <button type="button" class="toast-close" aria-label="Закрыть" @click="dismissToast">×</button>
  </div>
</template>
