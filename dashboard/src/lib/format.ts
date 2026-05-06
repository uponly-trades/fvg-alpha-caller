export const fmtUsd = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toFixed(2)}`;

export const fmtPct = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

export const fmtTime = (ms: number | null | undefined) =>
  ms == null ? "—" : new Date(Number(ms)).toISOString().replace("T", " ").slice(0, 19);

export const maskKey = (tail: string | null) =>
  tail ? `••••••••${tail}` : "—";
