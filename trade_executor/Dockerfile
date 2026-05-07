FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY trade_executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY trade_executor/pyproject.toml ./
COPY trade_executor/trade_executor/ ./trade_executor/

ENV PYTHONUNBUFFERED=1

EXPOSE 8014

CMD ["python", "-m", "trade_executor.main"]
