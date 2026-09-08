#!/usr/bin/env bash
set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [ -x "venv/bin/python" ]; then
  RUFF="venv/bin/python -m ruff"
elif command -v ruff >/dev/null 2>&1; then
  RUFF="ruff"
else
  echo "[format] ruff не найден, автоформат пропущен"
  exit 0
fi

PARTIAL="$(git diff --name-only --diff-filter=ACMR)"

FILES=""
while IFS= read -r -d '' file; do
  case "$file" in
    *.py) ;;
    *) continue ;;
  esac
  case "$file" in
    main.py|handlers/payments/*) continue ;;
  esac
  [ -f "$file" ] || continue
  if printf '%s\n' "$PARTIAL" | grep -Fxq "$file"; then
    echo "[format] $file правится частично — пропущен"
    continue
  fi
  FILES="$FILES$file"$'\n'
done < <(git diff --cached --name-only --diff-filter=ACMR -z)

[ -n "$FILES" ] || exit 0

BEFORE="$(printf '%s' "$FILES" | xargs -I {} git hash-object {} | tr '\n' ' ')"
printf '%s' "$FILES" | xargs $RUFF format --config pyproject.toml -q
printf '%s' "$FILES" | xargs $RUFF check --config pyproject.toml --fix -q || true
AFTER="$(printf '%s' "$FILES" | xargs -I {} git hash-object {} | tr '\n' ' ')"

if [ "$BEFORE" != "$AFTER" ]; then
  printf '%s' "$FILES" | xargs git add --
  echo "[format] файлы отформатированы и добавлены в коммит"
fi
