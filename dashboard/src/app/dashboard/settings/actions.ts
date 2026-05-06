"use server";
import { z } from "zod";
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";

const SettingsSchema = z.object({
  risk_pct: z.coerce.number().min(0.1).max(10),
  leverage: z.coerce.number().int().min(5).max(20),
  max_concurrent: z.coerce.number().int().min(1).max(10),
  daily_loss_cap_pct: z.coerce.number().min(1).max(50),
  enabled: z.boolean().optional().default(false),
});

export async function updateSettings(formData: FormData) {
  const user = await requireUser();
  const parsed = SettingsSchema.parse({
    risk_pct: formData.get("risk_pct"),
    leverage: formData.get("leverage"),
    max_concurrent: formData.get("max_concurrent"),
    daily_loss_cap_pct: formData.get("daily_loss_cap_pct"),
    enabled: formData.get("enabled") === "on",
  });
  const now = Date.now();
  await sql`
    UPDATE users SET
      risk_pct           = ${parsed.risk_pct},
      leverage           = ${parsed.leverage},
      max_concurrent     = ${parsed.max_concurrent},
      daily_loss_cap_pct = ${parsed.daily_loss_cap_pct},
      enabled            = ${parsed.enabled},
      updated_at         = ${now}
    WHERE id = ${user.id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user.id}, 'settings_update', ${sql.json(parsed)}, ${now})
  `;
  revalidatePath("/dashboard/settings");
}

export async function resumeFromPause() {
  const user = await requireUser();
  const now = Date.now();
  await sql`
    UPDATE users SET paused_until = NULL, pause_reason = NULL, updated_at = ${now}
    WHERE id = ${user.id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user.id}, 'resumed', ${sql.json({})}, ${now})
  `;
  revalidatePath("/dashboard/settings");
}
