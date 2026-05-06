import postgres from "postgres";
import { env } from "./env";

declare global {
  // eslint-disable-next-line no-var
  var __sql: ReturnType<typeof postgres> | undefined;
}

export const sql =
  global.__sql ??
  postgres(env.databaseUrl, { max: 5, idle_timeout: 20 });

if (process.env.NODE_ENV !== "production") global.__sql = sql;
