FROM python:3.11-slim

WORKDIR /app

# Install system deps for matplotlib/chart generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6-dev libpng-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1
ENV MPLCONFIGDIR=/tmp/mpl

CMD ["python", "main.py"]
