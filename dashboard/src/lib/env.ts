function need(k: string): string {
  const v = process.env[k];
  if (!v) throw new Error(`Missing env ${k}`);
  return v;
}

// Lazy getters so Next.js build-time page-data collection
// doesn't fail when env is unset (envs are only set at runtime).
export const env = {
  get databaseUrl() {
    return need("DATABASE_URL");
  },
  get botToken() {
    return need("TELEGRAM_BOT_TOKEN");
  },
  get botUsername() {
    return need("NEXT_PUBLIC_BOT_USERNAME");
  },
  get internalToken() {
    return need("INTERNAL_TOKEN");
  },
  get executorUrl() {
    return need("EXECUTOR_URL");
  },
};
