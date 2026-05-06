import { NextRequest, NextResponse } from "next/server";
import { verifyTelegramAuth, TgPayload } from "@/lib/telegram-verify";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { sql } from "@/lib/db";
import { env } from "@/lib/env";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const params = Object.fromEntries(req.nextUrl.searchParams.entries()) as TgPayload;
  if (!verifyTelegramAuth(params, env.botToken)) {
    return NextResponse.json({ error: "invalid_auth" }, { status: 401 });
  }
  const now = Date.now();
  const tgId = Number(params.id);
  const rows = await sql<{ id: number }[]>`
    INSERT INTO users (telegram_id, telegram_username, first_name, photo_url, created_at, updated_at)
    VALUES (
      ${tgId},
      ${(params.username as string) ?? null},
      ${(params.first_name as string) ?? null},
      ${(params.photo_url as string) ?? null},
      ${now}, ${now}
    )
    ON CONFLICT (telegram_id) DO UPDATE
      SET telegram_username = EXCLUDED.telegram_username,
          first_name        = EXCLUDED.first_name,
          photo_url         = EXCLUDED.photo_url,
          updated_at        = ${now}
    RETURNING id
  `;
  const userId = rows[0].id;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${userId}, 'login', ${sql.json({ tgId })}, ${now})
  `;
  const token = await createSession(userId);
  const res = NextResponse.redirect(new URL("/dashboard", req.url));
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    maxAge: 30 * 24 * 3600,
    path: "/",
  });
  return res;
}
