import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtPct, fmtTime, fmtUsd } from "@/lib/format";
import Link from "next/link";

export default async function TradesPage() {
  const user = await requireUser();
  const rows = await sql<any[]>`
    SELECT id, symbol, tf, direction, status, entry, sl_current, tp1, tp2,
           pnl_usdt, pnl_pct, opened_at, closed_at
    FROM user_trades
    WHERE user_id = ${user.id}
    ORDER BY opened_at DESC
    LIMIT 200
  `;
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-white mb-4">Trades</h1>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm text-zinc-200">
          <thead className="text-xs text-zinc-400 uppercase border-b border-zinc-800">
            <tr>
              <th className="px-3 py-2 text-left">Time</th>
              <th className="px-3 py-2 text-left">Symbol</th>
              <th className="px-3 py-2 text-left">TF</th>
              <th className="px-3 py-2 text-left">Dir</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-right">Entry</th>
              <th className="px-3 py-2 text-right">SL</th>
              <th className="px-3 py-2 text-right">TP2</th>
              <th className="px-3 py-2 text-right">PnL</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-zinc-900">
                <td className="px-3 py-2">{fmtTime(r.opened_at)}</td>
                <td className="px-3 py-2">{r.symbol}</td>
                <td className="px-3 py-2">{r.tf}</td>
                <td className={`px-3 py-2 ${r.direction === "long" ? "text-emerald-400" : "text-red-400"}`}>{r.direction}</td>
                <td className="px-3 py-2">{r.status}</td>
                <td className="px-3 py-2 text-right">{r.entry?.toFixed?.(4)}</td>
                <td className="px-3 py-2 text-right">{r.sl_current?.toFixed?.(4)}</td>
                <td className="px-3 py-2 text-right">{r.tp2?.toFixed?.(4)}</td>
                <td className="px-3 py-2 text-right">
                  <div>{fmtUsd(r.pnl_usdt)}</div>
                  <div className="text-xs text-zinc-500">{fmtPct(r.pnl_pct)}</div>
                </td>
                <td className="px-3 py-2"><Link className="text-blue-400" href={`/dashboard/trades/${r.id}`}>view</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
