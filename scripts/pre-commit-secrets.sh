#!/usr/bin/env bash
set -e

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

FORBIDDEN_FILES=(
  '^config\.py$'
  '^\.env$'
  '^\.env\.local$'
  '^\.env\.production$'
  '^\.env\.prod$'
  '^handlers/texts\.py$'
  '^settings/config\.py$'
  '^settings/texts\.py$'
  '(^|/)config_beta\.py$'
  '(^|/)texts_beta\.py$'
  '^alembic\.ini$'
  '^\.license_state$'
)

SECRET_PATTERNS=(
  '[0-9]{9,}:[A-Za-z0-9_-]{35}'
  '-----BEGIN [A-Z ]+PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}'
  'sk-[A-Za-z0-9]{20,}'
  'sk-ant-[A-Za-z0-9_-]{20,}'
  'ghp_[A-Za-z0-9]{36}'
  'gho_[A-Za-z0-9]{36}'
  'postgres(ql)?(\+asyncpg)?://[^:]+:[^@]{3,}@[^/]+'
  'mysql://[^:]+:[^@]{3,}@[^/]+'
  'redis://[^:]+:[^@]{3,}@[^/]+'
)

staged_files=$(git diff --cached --name-only --diff-filter=ACM)

if [ -z "$staged_files" ]; then
  exit 0
fi

violations=()

while IFS= read -r file; do
  [ -z "$file" ] && continue

  for forbidden in "${FORBIDDEN_FILES[@]}"; do
    if echo "$file" | grep -qE "$forbidden"; then
      violations+=("FORBIDDEN FILE: $file")
      break
    fi
  done

  if [ -f "$file" ]; then
    case "$file" in
      *.png|*.jpg|*.jpeg|*.gif|*.webp|*.svg|*.ico|*.woff|*.woff2|*.ttf|*.eot|*.zip|*.tar|*.gz|*.pdf|*.mp4|*.webm)
        continue
        ;;
      scripts/pre-commit-secrets.sh)
        continue
        ;;
    esac

    for pattern in "${SECRET_PATTERNS[@]}"; do
      if git show ":$file" 2>/dev/null | grep -E "$pattern" > /dev/null 2>&1; then
        matched=$(git show ":$file" 2>/dev/null | grep -nE "$pattern" | head -1)
        violations+=("SECRET in $file: $(echo "$matched" | cut -c1-100)")
      fi
    done
  fi
done <<< "$staged_files"

if [ ${#violations[@]} -gt 0 ]; then
  echo -e "${RED}════════════════════════════════════════════════${NC}"
  echo -e "${RED}║  COMMIT REJECTED: обнаружены секреты/forbidden files  ║${NC}"
  echo -e "${RED}════════════════════════════════════════════════${NC}"
  for v in "${violations[@]}"; do
    echo -e "  ${RED}✗${NC} $v"
  done
  echo ""
  echo -e "${YELLOW}Что делать:${NC}"
  echo -e "  1. Проверь staged файлы: ${YELLOW}git diff --cached${NC}"
  echo -e "  2. Убери чувствительные данные или используй .env (gitignored)"
  echo -e "  3. Если false positive и нужно форсировать коммит:"
  echo -e "     ${YELLOW}git commit --no-verify${NC}"
  exit 1
fi

exit 0
