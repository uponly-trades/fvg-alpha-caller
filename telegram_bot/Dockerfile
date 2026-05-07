FROM python:3.11-slim

WORKDIR /app

COPY telegram_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_bot/ ./telegram_bot/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "telegram_bot.main"]
