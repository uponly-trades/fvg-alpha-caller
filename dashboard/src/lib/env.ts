function need(k: string): string {
  const v = process.env[k];
  if (!v) throw new Error(`Missing env ${k}`);
  return v;
}

export const env = {
  databaseUrl: need("DATABASE_URL"),
  botToken: need("TELEGRAM_BOT_TOKEN"),
  botUsername: need("NEXT_PUBLIC_BOT_USERNAME"),
  internalToken: need("INTERNAL_TOKEN"),
  executorUrl: need("EXECUTOR_URL"),
};
