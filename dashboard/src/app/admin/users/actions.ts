"use server";
import { z } from "zod";
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { requireAdmin } from "@/lib/auth";

const Schema = z.object({
  user_id: z.coerce.number().int().positive(),
  reason: z.string().min(1).max(200),
});

export async function forcePauseUser(formData: FormData) {
  const admin = await requireAdmin();
  const { user_id, reason } = Schema.parse({
    user_id: formData.get("user_id"),
    reason: formData.get("reason"),
  });
  const now = Date.now();
  await sql`
    UPDATE users
    SET paused_until = 9999999999999, pause_reason = ${reason}, updated_at = ${now}
    WHERE id = ${user_id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user_id}, 'paused', ${sql.json({ reason, by_admin: admin.id })}, ${now})
  `;
  revalidatePath("/admin/users");
}
