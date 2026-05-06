import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtPct, fmtTime, fmtUsd } from "@/lib/format";
import { notFound } from "next/navigation";

export default async function TradeDetail({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  const { id } = await params;
  const rows = await sql<any[]>`
    SELECT * FROM user_trades WHERE id = ${id} AND user_id = ${user.id}
  `;
  const t = rows[0];
  if (!t) notFound();
  return (
    <div className="p-6 space-y-3 text-zinc-200">
      <h1 className="text-2xl font-semibold">{t.symbol} {t.tf} <span className={t.direction === "long" ? "text-emerald-400" : "text-red-400"}>{t.direction}</span></h1>
      <div className="grid grid-cols-2 gap-2 max-w-md">
        <Row k="status" v={t.status} />
        <Row k="entry" v={t.entry?.toFixed(4)} />
        <Row k="sl_current" v={t.sl_current?.toFixed(4)} />
        <Row k="tp1" v={t.tp1?.toFixed(4)} />
        <Row k="tp2" v={t.tp2?.toFixed(4)} />
        <Row k="qty" v={t.qty} />
        <Row k="leverage" v={`${t.leverage}x`} />
        <Row k="margin" v={fmtUsd(t.margin_usdt)} />
        <Row k="notional" v={fmtUsd(t.notional_usdt)} />
        <Row k="pnl" v={`${fmtUsd(t.pnl_usdt)} (${fmtPct(t.pnl_pct)})`} />
        <Row k="opened" v={fmtTime(t.opened_at)} />
        <Row k="closed" v={fmtTime(t.closed_at)} />
      </div>
      {t.error_msg && <div className="bg-red-950 p-3 rounded text-red-200 text-sm">{t.error_msg}</div>}
    </div>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <>
      <div className="text-zinc-500">{k}</div>
      <div>{v ?? "—"}</div>
    </>
  );
}
