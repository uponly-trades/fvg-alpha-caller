import { sql } from "@/lib/db";
import { env } from "@/lib/env";

async function execHealth(): Promise<{ ok: boolean; status: number }> {
  try {
    const r = await fetch(`${env.executorUrl}/healthz`, { cache: "no-store" });
    return { ok: r.ok, status: r.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

export default async function AdminSystemPage() {
  const health = await execHealth();
  const [last] = await sql<any[]>`
    SELECT MAX(created_at) AS last FROM signal_decisions WHERE valid = true
  `;
  const lastAgeMs = last?.last ? Date.now() - Number(last.last) : null;
  const [errs] = await sql<any[]>`
    SELECT COUNT(*)::int AS n
    FROM user_trades
    WHERE status LIKE 'error_%'
      AND opened_at > ${Date.now() - 24 * 3600 * 1000}
  `;
  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">System</h1>
      <Stat label="Executor /healthz" value={health.ok ? `OK (${health.status})` : `DOWN (${health.status})`} bad={!health.ok} />
      <Stat label="Last signal age" value={lastAgeMs == null ? "—" : `${Math.floor(lastAgeMs / 1000)}s ago`} bad={(lastAgeMs ?? 0) > 1800_000} />
      <Stat label="Errors (24h)" value={String(errs?.n ?? 0)} bad={(errs?.n ?? 0) > 0} />
    </div>
  );
}

function Stat({ label, value, bad }: { label: string; value: string; bad: boolean }) {
  return (
    <div className={`p-4 rounded-2xl ${bad ? "bg-red-950 border border-red-800" : "bg-zinc-900"}`}>
      <div className="text-zinc-400 text-sm">{label}</div>
      <div className="text-2xl">{value}</div>
    </div>
  );
}
