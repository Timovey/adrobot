<script setup lang="ts">
import { computed, ref } from 'vue'
import { getStoredApiToken, hasEnvApiToken, setStoredApiToken } from '../api'
import type { ToastPayload } from '../types'

const emit = defineEmits<{ toast: [payload: ToastPayload] }>()

const open = ref(false)
const draft = ref(getStoredApiToken())
const saved = ref(getStoredApiToken())

const status = computed(() => {
  if (saved.value) return 'токен сохранён'
  if (hasEnvApiToken()) return 'токен из env'
  return 'без токена'
})

function save(): void {
  setStoredApiToken(draft.value)
  saved.value = getStoredApiToken()
  draft.value = saved.value
  open.value = false
  emit('toast', { message: saved.value ? 'API-токен сохранён' : 'API-токен удалён', type: 'ok' })
}

function clear(): void {
  draft.value = ''
  save()
}
</script>

<template>
  <div class="settings">
    <button type="button" class="secondary small" :aria-expanded="open" @click="open = !open">
      Настройки · {{ status }}
    </button>
    <form v-if="open" class="settings-panel" @submit.prevent="save">
      <label class="field">
        API-токен (передаётся как Bearer)
        <input
          v-model="draft"
          type="password"
          autocomplete="off"
          placeholder="Значение ADROBOT_API_TOKEN"
        />
      </label>
      <p class="hint">
        Хранится в localStorage этого браузера и имеет приоритет над
        <code>VITE_ADROBOT_API_TOKEN</code>. Оставьте пустым, если на сервере токен не настроен.
      </p>
      <div class="btn-group">
        <button type="submit">Сохранить</button>
        <button type="button" class="secondary" :disabled="!saved" @click="clear">Очистить</button>
      </div>
    </form>
  </div>
</template>
