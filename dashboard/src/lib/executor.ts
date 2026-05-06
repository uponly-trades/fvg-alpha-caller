import { env } from "./env";

export async function encryptViaExecutor(plaintext: string): Promise<Buffer> {
  const r = await fetch(`${env.executorUrl}/encrypt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Token": env.internalToken,
    },
    body: JSON.stringify({ plaintext }),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`executor /encrypt ${r.status}`);
  const j = (await r.json()) as { ciphertext_b64: string };
  return Buffer.from(j.ciphertext_b64, "base64");
}
