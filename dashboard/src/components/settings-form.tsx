"use client";
import { updateSettings } from "@/app/dashboard/settings/actions";

type Props = {
  defaults: {
    risk_pct: number;
    leverage: number;
    max_concurrent: number;
    daily_loss_cap_pct: number;
    enabled: boolean;
  };
};

export function SettingsForm({ defaults }: Props) {
  return (
    <form action={updateSettings} className="space-y-4 max-w-md">
      <Field name="risk_pct" label="Risk % per trade" defaultValue={defaults.risk_pct} step={0.1} />
      <Field name="leverage" label="Leverage (5–20x)" defaultValue={defaults.leverage} step={1} />
      <Field name="max_concurrent" label="Max concurrent trades" defaultValue={defaults.max_concurrent} step={1} />
      <Field name="daily_loss_cap_pct" label="Daily loss cap %" defaultValue={defaults.daily_loss_cap_pct} step={0.5} />
      <label className="flex items-center gap-2 text-zinc-200">
        <input type="checkbox" name="enabled" defaultChecked={defaults.enabled} />
        Enable live trading
      </label>
      <button className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded">
        Save
      </button>
    </form>
  );
}

function Field({ name, label, defaultValue, step }: { name: string; label: string; defaultValue: number; step: number }) {
  return (
    <label className="block text-zinc-200">
      <span className="text-sm">{label}</span>
      <input
        type="number"
        name={name}
        defaultValue={defaultValue}
        step={step}
        className="mt-1 block w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2"
      />
    </label>
  );
}
