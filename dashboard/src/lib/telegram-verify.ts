import crypto from "node:crypto";

export type TgPayload = {
  id: string | number;
  first_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: string | number;
  hash: string;
  [k: string]: unknown;
};

export function verifyTelegramAuth(payload: TgPayload, botToken: string): boolean {
  const { hash, ...rest } = payload;
  const secret = crypto.createHash("sha256").update(botToken).digest();
  const dataCheck = Object.keys(rest)
    .sort()
    .map((k) => `${k}=${rest[k as keyof typeof rest]}`)
    .join("\n");
  const expected = crypto
    .createHmac("sha256", secret)
    .update(dataCheck)
    .digest("hex");

  const hashBuf = Buffer.from(hash, "hex");
  const expectedBuf = Buffer.from(expected, "hex");
  if (hashBuf.length !== expectedBuf.length) return false;
  if (!crypto.timingSafeEqual(hashBuf, expectedBuf)) return false;
  const authDate = Number(payload.auth_date);
  const now = Math.floor(Date.now() / 1000);
  if (now - authDate > 86400) return false;
  return true;
}
