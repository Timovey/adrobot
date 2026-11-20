/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional API token sent to the backend (see `src/api.ts`). Baked into the bundle at build time. */
  readonly VITE_ADROBOT_API_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
