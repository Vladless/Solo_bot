FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY . .

RUN rm -rf /app/venv \
    && python -m venv /app/venv \
    && /app/venv/bin/pip install --upgrade pip \
    && /app/venv/bin/pip install -r requirements.txt

CMD ["/app/venv/bin/python", "main.py"]
