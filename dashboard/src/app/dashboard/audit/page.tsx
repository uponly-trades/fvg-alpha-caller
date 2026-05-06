import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtTime } from "@/lib/format";

export default async function AuditPage() {
  const user = await requireUser();
  const rows = await sql<any[]>`
    SELECT id, action, payload, created_at
    FROM user_audit_log
    WHERE user_id = ${user.id}
    ORDER BY created_at DESC
    LIMIT 100
  `;
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-white mb-4">Audit log</h1>
      <ul className="space-y-1 text-sm font-mono text-zinc-300">
        {rows.map((r) => (
          <li key={r.id} className="flex gap-3">
            <span className="text-zinc-500">{fmtTime(r.created_at)}</span>
            <span className="text-emerald-400">{r.action}</span>
            <span className="text-zinc-400">{JSON.stringify(r.payload)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
