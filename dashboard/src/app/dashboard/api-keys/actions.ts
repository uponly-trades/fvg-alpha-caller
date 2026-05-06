"use server";
import { z } from "zod";
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { encryptViaExecutor } from "@/lib/executor";

const KeySchema = z.object({
  api_key: z.string().min(20).max(128),
  api_secret: z.string().min(20).max(128),
});

export async function rotateKeys(formData: FormData) {
  const user = await requireUser();
  const parsed = KeySchema.parse({
    api_key: formData.get("api_key"),
    api_secret: formData.get("api_secret"),
  });
  const keyEnc = await encryptViaExecutor(parsed.api_key);
  const secEnc = await encryptViaExecutor(parsed.api_secret);
  const tail = parsed.api_key.slice(-4);
  const now = Date.now();
  await sql`
    UPDATE users SET
      binance_api_key_enc    = ${keyEnc},
      binance_api_secret_enc = ${secEnc},
      api_key_tail           = ${tail},
      updated_at             = ${now}
    WHERE id = ${user.id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user.id}, 'keys_rotated', ${sql.json({ tail })}, ${now})
  `;
  revalidatePath("/dashboard/api-keys");
}
