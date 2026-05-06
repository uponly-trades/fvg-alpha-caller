import { NextResponse } from "next/server";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function GET() {
  await requireUser();
  const rows = await sql<any[]>`
    SELECT id, symbol, tf, direction, event_type, entry, sl, tp1, tp2, created_at
    FROM kronos_decisions
    WHERE valid = true
    ORDER BY created_at DESC
    LIMIT 50
  `;
  return NextResponse.json({ rows });
}
