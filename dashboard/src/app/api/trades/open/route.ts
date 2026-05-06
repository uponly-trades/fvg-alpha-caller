import { NextResponse } from "next/server";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function GET() {
  const user = await requireUser();
  const rows = await sql<{ count: number }[]>`
    SELECT COUNT(*)::int AS count FROM user_trades
    WHERE user_id = ${user.id} AND status IN ('opening','open','tp1_trailed')
  `;
  return NextResponse.json({ open: rows[0].count });
}
