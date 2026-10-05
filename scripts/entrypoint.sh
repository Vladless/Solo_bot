#!/bin/sh
set -e

BOT_WORKERS="${BOT_WORKERS:-1}"
BASE="${LB_PORT:-3001}"
PYTHON="${PYTHON:-/app/venv/bin/python}"

if [ "$BOT_WORKERS" -le 1 ]; then
  exec "$PYTHON" main.py
fi

PORTS=""
i=0
while [ $i -lt "$BOT_WORKERS" ]; do
  p=$((BASE + i + 1))
  [ -n "$PORTS" ] && PORTS="$PORTS,"
  PORTS="${PORTS}${p}"
  i=$((i + 1))
done

export WORKER_PORTS="$PORTS"
export LB_PORT="$BASE"
export LB_HOST="${LB_HOST:-0.0.0.0}"

i=0
while [ $i -lt "$BOT_WORKERS" ]; do
  export WEBAPP_PORT=$((BASE + i + 1))
  "$PYTHON" main.py &
  i=$((i + 1))
done

exec "$PYTHON" scripts/load_balancer.py
