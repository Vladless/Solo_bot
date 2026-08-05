FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /app/venv \
    && /app/venv/bin/pip install --upgrade pip \
    && /app/venv/bin/pip install -r requirements.txt \
    && touch /app/venv/.installed

COPY . .

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/backups /app/logs /app/modules /app/static/web_uploads /app/alembic/versions \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 3001 3004

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["/app/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3004/api/health', timeout=4)"]

CMD ["/app/venv/bin/python", "main.py"]
