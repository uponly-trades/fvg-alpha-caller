import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { CumPnlChart, WrBySymbolChart } from "@/components/stats-charts";

export default async function StatsPage() {
  const user = await requireUser();
  const daily = await sql<any[]>`
    SELECT day::text, realized_pnl_usdt
    FROM user_daily_pnl
    WHERE user_id = ${user.id}
    ORDER BY day
  `;
  let cum = 0;
  const cumData = daily.map((d) => ({ day: d.day, cum: (cum += Number(d.realized_pnl_usdt)) }));

  const bySym = await sql<any[]>`
    SELECT symbol,
      COUNT(*) FILTER (WHERE status='closed_tp2')::float
        / NULLIF(COUNT(*) FILTER (WHERE status IN ('closed_tp2','closed_sl','closed_breakeven')), 0) AS wr
    FROM user_trades
    WHERE user_id = ${user.id}
    GROUP BY symbol
    ORDER BY symbol
  `;

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-2xl font-semibold text-white">Stats</h1>
      <section>
        <h2 className="text-zinc-300 mb-2">Cumulative PnL ($)</h2>
        <CumPnlChart data={cumData} />
      </section>
      <section>
        <h2 className="text-zinc-300 mb-2">Win rate by symbol</h2>
        <WrBySymbolChart data={bySym.map((r) => ({ symbol: r.symbol, wr: Number(r.wr) || 0 }))} />
      </section>
    </div>
  );
}
