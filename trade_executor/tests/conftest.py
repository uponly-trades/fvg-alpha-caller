import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")  # 32-byte base64
os.environ.setdefault("BINANCE_PROXY_URL", "http://proxy:8080")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:bot-token")
