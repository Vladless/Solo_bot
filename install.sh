#!/bin/bash
set -e

REPO="Vladless/Solo_bot"
BRANCH="main"
INSTALL_DIR="/root/solobot"
CLI="cli_launcher.py"
CMD_NAME="solobot"
CMD_PATH="/usr/local/bin/$CMD_NAME"

command -v python3 >/dev/null 2>&1 || {
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq python3 git curl >/dev/null 2>&1
}

command -v git >/dev/null 2>&1 || {
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq git >/dev/null 2>&1
}

if [ ! -f "$INSTALL_DIR/$CLI" ]; then
    echo "Загрузка Solo Bot..."
    git clone --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" "$INSTALL_DIR" 2>/dev/null || {
        echo "Ошибка загрузки. Проверьте подключение к интернету."
        exit 1
    }
fi

cat > "$CMD_PATH" << EOF
#!/bin/bash
cd $INSTALL_DIR && python3 $CLI "\$@"
EOF
chmod +x "$CMD_PATH"

cd "$INSTALL_DIR"
exec python3 "$CLI"
