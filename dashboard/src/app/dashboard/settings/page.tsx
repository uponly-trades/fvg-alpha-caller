import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { SettingsForm } from "@/components/settings-form";
import { resumeFromPause } from "./actions";

export default async function SettingsPage() {
  const user = await requireUser();
  const rows = await sql<any[]>`
    SELECT risk_pct, leverage, max_concurrent, daily_loss_cap_pct, enabled,
           paused_until, pause_reason
    FROM users WHERE id = ${user.id}
  `;
  const u = rows[0];
  const isPaused = u.paused_until && Number(u.paused_until) > Date.now();
  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold text-white">Settings</h1>
      {isPaused && (
        <div className="bg-red-950 border border-red-800 p-4 rounded">
          <p className="text-red-200">Paused: {u.pause_reason}</p>
          <form action={resumeFromPause}>
            <button className="mt-2 bg-red-700 hover:bg-red-600 text-white px-3 py-1 rounded">
              Resume
            </button>
          </form>
        </div>
      )}
      <SettingsForm defaults={u} />
    </div>
  );
}
