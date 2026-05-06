"use client";
import useSWR from "swr";

const fetcher = (u: string) => fetch(u).then((r) => r.json());

export default function SignalsPage() {
  const { data } = useSWR("/api/signals", fetcher, { refreshInterval: 5000 });
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-white mb-4">Live signals</h1>
      <ul className="space-y-2">
        {(data?.rows ?? []).map((r: any) => (
          <li key={r.id} className="bg-zinc-900 px-3 py-2 rounded flex justify-between text-sm">
            <span>
              <span className={r.direction === "long" ? "text-emerald-400" : "text-red-400"}>{r.direction.toUpperCase()}</span>
              {" "}
              <strong>{r.symbol}</strong> {r.tf} <span className="text-zinc-500">{r.event_type}</span>
            </span>
            <span className="text-zinc-400">{new Date(Number(r.created_at)).toLocaleTimeString()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
