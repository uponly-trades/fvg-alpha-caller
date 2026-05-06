import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtPct, fmtUsd } from "@/lib/format";

export default async function Overview() {
  const user = await requireUser();
  const today = new Date().toISOString().slice(0, 10);
  const [pnl] = await sql<any[]>`
    SELECT realized_pnl_usdt, realized_pnl_pct, trades_count, wins_count
    FROM user_daily_pnl WHERE user_id = ${user.id} AND day = ${today}::date
  `;
  const [open] = await sql<any[]>`
    SELECT COUNT(*)::int n FROM user_trades
    WHERE user_id = ${user.id} AND status IN ('opening','open','tp1_trailed')
  `;
  const [wr] = await sql<any[]>`
    SELECT
      COUNT(*) FILTER (WHERE status = 'closed_tp2')::float
        / NULLIF(COUNT(*) FILTER (WHERE status IN ('closed_tp2','closed_sl','closed_breakeven')), 0) AS wr_30d
    FROM user_trades
    WHERE user_id = ${user.id} AND closed_at > ${Date.now() - 30 * 86400 * 1000}
  `;
  return (
    <div className="p-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
      <Card title="Today PnL" big={fmtUsd(pnl?.realized_pnl_usdt)} sub={fmtPct(pnl?.realized_pnl_pct)} />
      <Card title="Open trades" big={String(open?.n ?? 0)} sub="" />
      <Card title="Win rate (30d)" big={pnl ? fmtPct((wr?.wr_30d ?? 0) * 100) : "—"} sub="" />
    </div>
  );
}

function Card({ title, big, sub }: { title: string; big: string; sub: string }) {
  return (
    <div className="bg-zinc-900 p-4 rounded-2xl">
      <div className="text-zinc-400 text-sm">{title}</div>
      <div className="text-3xl text-white mt-1">{big}</div>
      <div className="text-zinc-500 text-sm">{sub}</div>
    </div>
  );
}
