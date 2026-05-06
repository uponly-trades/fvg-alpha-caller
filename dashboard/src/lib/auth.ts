import { cookies } from "next/headers";
import crypto from "node:crypto";
import { sql } from "./db";

export const SESSION_COOKIE = "session";
const SESSION_TTL_MS = 30 * 24 * 3600 * 1000;

export async function createSession(userId: number): Promise<string> {
  const token = crypto.randomBytes(32).toString("hex");
  const now = Date.now();
  await sql`
    INSERT INTO sessions (token, user_id, created_at, expires_at)
    VALUES (${token}, ${userId}, ${now}, ${now + SESSION_TTL_MS})
  `;
  return token;
}

export type SessionUser = {
  id: number;
  telegram_id: number;
  telegram_username: string | null;
  first_name: string | null;
  is_admin: boolean;
};

export async function getSessionUser(): Promise<SessionUser | null> {
  const c = await cookies();
  const token = c.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  const rows = await sql<SessionUser[]>`
    SELECT u.id, u.telegram_id, u.telegram_username, u.first_name, u.is_admin
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token = ${token} AND s.expires_at > ${Date.now()}
  `;
  return rows[0] ?? null;
}

export async function destroySession(token: string): Promise<void> {
  await sql`DELETE FROM sessions WHERE token = ${token}`;
}

export async function requireUser(): Promise<SessionUser> {
  const u = await getSessionUser();
  if (!u) throw new Error("Unauthorized");
  return u;
}

export async function requireAdmin(): Promise<SessionUser> {
  const u = await requireUser();
  if (!u.is_admin) throw new Error("Forbidden");
  return u;
}
