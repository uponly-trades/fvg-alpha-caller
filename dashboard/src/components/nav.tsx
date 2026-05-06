import Link from "next/link";
import { SessionUser } from "@/lib/auth";

const items = [
  ["/dashboard", "Overview"],
  ["/dashboard/signals", "Signals"],
  ["/dashboard/trades", "Trades"],
  ["/dashboard/stats", "Stats"],
  ["/dashboard/settings", "Settings"],
  ["/dashboard/api-keys", "API keys"],
  ["/dashboard/audit", "Audit"],
];

export function Nav({ user }: { user: SessionUser }) {
  return (
    <nav className="border-b border-zinc-800 bg-zinc-950">
      <div className="px-6 py-3 flex items-center justify-between">
        <div className="flex gap-4 text-sm">
          {items.map(([href, label]) => (
            <Link key={href} href={href} className="text-zinc-300 hover:text-white">{label}</Link>
          ))}
          {user.is_admin && (
            <>
              <span className="text-zinc-700">|</span>
              <Link href="/admin/users" className="text-amber-400 hover:text-amber-300">Admin users</Link>
              <Link href="/admin/system" className="text-amber-400 hover:text-amber-300">Admin system</Link>
            </>
          )}
        </div>
        <div className="text-xs text-zinc-400">@{user.telegram_username ?? user.telegram_id}</div>
      </div>
    </nav>
  );
}
