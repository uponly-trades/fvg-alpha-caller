"""Shared test fixtures: ensure env vars exist before any module import.

Tests assert legacy fixed-RR TP behavior; structural magnet mode requires real
swing data to produce a magnet, so disable strict magnet-required gate during
unit tests. Tests that target the structural path (test_strategy_v2_structural)
exercise helpers directly and remain unaffected.
"""
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

# Allow legacy tests to keep their entry±risk*RR assertions. Production
# deployments set this to 1 (strict).
os.environ.setdefault("V2_TP_MAGNET_REQUIRED", "0")
