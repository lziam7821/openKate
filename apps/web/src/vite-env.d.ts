interface ImportMetaEnv {
  readonly VITE_GATEWAY_URL?: string;
  readonly VITE_ACCESS_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
