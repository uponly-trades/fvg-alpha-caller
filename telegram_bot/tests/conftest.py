import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:bot-token")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal")
os.environ.setdefault("EXECUTOR_URL", "http://executor")
