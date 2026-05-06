import { sql } from "@/lib/db";
import { fmtTime } from "@/lib/format";
import { forcePauseUser } from "./actions";

export default async function AdminUsersPage() {
  const users = await sql<any[]>`
    SELECT id, telegram_id, telegram_username, first_name, enabled,
           paused_until, pause_reason, created_at, api_key_tail
    FROM users ORDER BY created_at DESC
  `;
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold mb-4">All users</h1>
      <table className="min-w-full text-sm">
        <thead className="text-xs text-zinc-400 uppercase border-b border-zinc-800">
          <tr>
            <th className="px-3 py-2 text-left">id</th>
            <th className="px-3 py-2 text-left">tg</th>
            <th className="px-3 py-2 text-left">name</th>
            <th className="px-3 py-2 text-left">key</th>
            <th className="px-3 py-2 text-left">enabled</th>
            <th className="px-3 py-2 text-left">paused</th>
            <th className="px-3 py-2 text-left">joined</th>
            <th className="px-3 py-2 text-left">force-pause</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className="border-b border-zinc-900">
              <td className="px-3 py-2">{u.id}</td>
              <td className="px-3 py-2">@{u.telegram_username ?? u.telegram_id}</td>
              <td className="px-3 py-2">{u.first_name}</td>
              <td className="px-3 py-2 font-mono text-xs">{u.api_key_tail ?? "—"}</td>
              <td className="px-3 py-2">{u.enabled ? "✓" : "—"}</td>
              <td className="px-3 py-2">{u.paused_until && Number(u.paused_until) > Date.now() ? u.pause_reason : "—"}</td>
              <td className="px-3 py-2">{fmtTime(u.created_at)}</td>
              <td className="px-3 py-2">
                <form action={forcePauseUser} className="flex gap-2">
                  <input type="hidden" name="user_id" value={u.id} />
                  <input name="reason" placeholder="reason" className="bg-zinc-900 border border-zinc-700 px-2 py-1 rounded text-xs" />
                  <button className="bg-red-700 hover:bg-red-600 text-white px-2 py-1 rounded text-xs">pause</button>
                </form>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
