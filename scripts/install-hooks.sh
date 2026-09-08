#!/usr/bin/env bash
set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SCRIPTS_DIR="$REPO_ROOT/scripts"

if [ ! -d "$HOOKS_DIR" ]; then
  echo "Error: $HOOKS_DIR не найден. Это git-репозиторий?"
  exit 1
fi

cat > "$HOOKS_DIR/pre-commit" <<'HOOK'
#!/usr/bin/env bash
set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
"$REPO_ROOT/scripts/pre-commit-format.sh"
exec "$REPO_ROOT/scripts/pre-commit-secrets.sh"
HOOK

cat > "$HOOKS_DIR/pre-push" <<'HOOK'
#!/usr/bin/env bash
set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
exec "$REPO_ROOT/scripts/pre-push-format.sh"
HOOK

chmod +x "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/pre-push"
echo "✓ pre-commit hook установлен"
echo "  → $HOOKS_DIR/pre-commit"
echo "✓ pre-push hook установлен"
echo "  → $HOOKS_DIR/pre-push"
echo ""
echo "commit: автоформат staged-файлов (ruff format + автофиксы) и проверка секретов."
echo "push:   проверка, что в уходящих коммитах нет неотформатированного кода."
echo "Если нужно пропустить (осознанно): git commit --no-verify / git push --no-verify"
