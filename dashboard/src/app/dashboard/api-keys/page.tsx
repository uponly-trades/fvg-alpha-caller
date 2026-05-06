import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { rotateKeys } from "./actions";
import { maskKey } from "@/lib/format";

export default async function ApiKeysPage() {
  const user = await requireUser();
  const rows = await sql<{ api_key_tail: string | null }[]>`
    SELECT api_key_tail FROM users WHERE id = ${user.id}
  `;
  const tail = rows[0]?.api_key_tail ?? null;

  return (
    <div className="p-6 space-y-6 max-w-md">
      <h1 className="text-2xl font-semibold text-white">Binance API Keys</h1>
      <p className="text-zinc-400">Current key: <code className="text-zinc-200">{maskKey(tail)}</code></p>
      <div className="bg-amber-950 border border-amber-800 p-4 rounded text-amber-200 text-sm">
        Whitelist proxy IP on your Binance API. Permissions: Futures Trading + Read. NEVER enable Withdraw.
      </div>
      <form action={rotateKeys} className="space-y-3">
        <label className="block text-zinc-200">
          <span className="text-sm">API Key</span>
          <input name="api_key" required className="mt-1 block w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 font-mono text-sm" />
        </label>
        <label className="block text-zinc-200">
          <span className="text-sm">API Secret</span>
          <input name="api_secret" type="password" required className="mt-1 block w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 font-mono text-sm" />
        </label>
        <button className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded">
          Save keys
        </button>
      </form>
    </div>
  );
}
