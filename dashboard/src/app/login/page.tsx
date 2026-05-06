import Script from "next/script";
import { env } from "@/lib/env";

export const dynamic = "force-dynamic";

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950">
      <div className="bg-zinc-900 p-8 rounded-2xl text-center space-y-4">
        <h1 className="text-2xl font-semibold text-white">FVG Live Trader</h1>
        <p className="text-zinc-400">Sign in with Telegram</p>
        <div id="tg-login-container" />
        <Script
          id="tg-login"
          strategy="afterInteractive"
          src="https://telegram.org/js/telegram-widget.js?22"
          data-telegram-login={env.botUsername}
          data-size="large"
          data-auth-url="/api/auth/telegram"
          data-request-access="write"
        />
      </div>
    </div>
  );
}
