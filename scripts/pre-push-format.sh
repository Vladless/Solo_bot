#!/usr/bin/env bash
set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [ -x "venv/bin/python" ]; then
  RUFF="venv/bin/python -m ruff"
elif command -v ruff >/dev/null 2>&1; then
  RUFF="ruff"
else
  exit 0
fi

ZERO="0000000000000000000000000000000000000000"
CHANGED=""

while read -r _local_ref local_sha _remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue
  if [ "$remote_sha" = "$ZERO" ]; then
    base="$(git merge-base "$local_sha" origin/main 2>/dev/null || echo "$local_sha^")"
  else
    base="$remote_sha"
  fi
  CHANGED="$CHANGED$(git diff --name-only --diff-filter=ACMR "$base" "$local_sha" -- '*.py' 2>/dev/null || true)"$'\n'
done

FILES=""
while IFS= read -r file; do
  [ -n "$file" ] || continue
  case "$file" in
    main.py|handlers/payments/*) continue ;;
  esac
  [ -f "$file" ] || continue
  FILES="$FILES$file"$'\n'
done < <(printf '%s' "$CHANGED" | sort -u)

[ -n "$FILES" ] || exit 0

if ! printf '%s' "$FILES" | xargs $RUFF format --check --config pyproject.toml -q; then
  echo ""
  echo "[format] в уходящих коммитах есть неотформатированные файлы."
  echo "         Запустите: make format  (затем закоммитьте изменения)"
  echo "         Пропустить осознанно: git push --no-verify"
  exit 1
fi
