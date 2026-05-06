"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

export function CumPnlChart({ data }: { data: { day: string; cum: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data}>
        <XAxis dataKey="day" stroke="#666" />
        <YAxis stroke="#666" />
        <Tooltip />
        <Line type="monotone" dataKey="cum" stroke="#10b981" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function WrBySymbolChart({ data }: { data: { symbol: string; wr: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <XAxis dataKey="symbol" stroke="#666" />
        <YAxis stroke="#666" domain={[0, 1]} />
        <Tooltip />
        <Bar dataKey="wr" fill="#3b82f6" />
      </BarChart>
    </ResponsiveContainer>
  );
}
