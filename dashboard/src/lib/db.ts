import postgres from "postgres";
import { env } from "./env";

declare global {
  // eslint-disable-next-line no-var
  var __sql: ReturnType<typeof postgres> | undefined;
}

type SqlFn = ReturnType<typeof postgres>;

function getSql(): SqlFn {
  if (global.__sql) return global.__sql;
  const instance = postgres(env.databaseUrl, { max: 5, idle_timeout: 20 });
  if (process.env.NODE_ENV !== "production") global.__sql = instance;
  return instance;
}

// Lazy proxy: connection is created on first call, not at module-load.
// Avoids Next.js build-time crash when DATABASE_URL is unset during
// static page-data collection.
export const sql: SqlFn = new Proxy(
  function () {} as unknown as SqlFn,
  {
    apply(_t, _this, args: unknown[]) {
      const fn = getSql() as unknown as (...a: unknown[]) => unknown;
      return fn(...args);
    },
    get(_t, prop) {
      const inst = getSql() as unknown as Record<string | symbol, unknown>;
      return inst[prop as string];
    },
  },
) as SqlFn;
