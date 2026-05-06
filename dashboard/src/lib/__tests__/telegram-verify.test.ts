import { describe, it, expect } from "vitest";
import crypto from "node:crypto";
import { verifyTelegramAuth } from "../telegram-verify";

function sign(payload: Record<string, string>, token: string) {
  const secret = crypto.createHash("sha256").update(token).digest();
  const dataCheck = Object.keys(payload)
    .sort()
    .map((k) => `${k}=${payload[k]}`)
    .join("\n");
  return crypto.createHmac("sha256", secret).update(dataCheck).digest("hex");
}

describe("verifyTelegramAuth", () => {
  const token = "TEST_BOT_TOKEN";
  const now = Math.floor(Date.now() / 1000);

  it("accepts a valid payload", () => {
    const payload = { id: "123", first_name: "Bob", auth_date: String(now) };
    const hash = sign(payload, token);
    expect(verifyTelegramAuth({ ...payload, hash }, token)).toBe(true);
  });

  it("rejects bad hash", () => {
    const payload = { id: "123", first_name: "Bob", auth_date: String(now) };
    expect(verifyTelegramAuth({ ...payload, hash: "deadbeef" }, token)).toBe(false);
  });

  it("rejects stale auth_date", () => {
    const stale = now - 90000;
    const payload = { id: "123", first_name: "Bob", auth_date: String(stale) };
    const hash = sign(payload, token);
    expect(verifyTelegramAuth({ ...payload, hash }, token)).toBe(false);
  });
});
