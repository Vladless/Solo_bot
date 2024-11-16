#!/bin/bash

# Цвета для вывода
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}PostgreSQL Installation and Setup Script by izzzzzi${NC}"
echo "----------------------------------------"

# Запрос данных у пользователя
read -p "Enter PostgreSQL port (default: 5432): " DB_PORT
read -p "Enter database name: " DB_NAME
read -p "Enter database user: " DB_USER
read -s -p "Enter database password: " DB_PASS
echo
read -s -p "Confirm database password: " DB_PASS_CONFIRM
echo

# Проверка паролей
if [ "$DB_PASS" != "$DB_PASS_CONFIRM" ]; then
    echo -e "${RED}Passwords do not match!${NC}"
    exit 1
fi

# Использование значения по умолчанию для порта
DB_PORT=${DB_PORT:-5432}

# Установка PostgreSQL
echo -e "${GREEN}Installing PostgreSQL...${NC}"
apt-get update
apt-get install -y postgresql postgresql-contrib

# Остановка PostgreSQL для изменения конфигурации
systemctl stop postgresql

# Настройка PostgreSQL для внешних подключений
echo -e "${GREEN}Configuring PostgreSQL...${NC}"

# Настройка postgresql.conf
PG_VERSION=$(ls /etc/postgresql/)
PG_CONF="/etc/postgresql/$PG_VERSION/main/postgresql.conf"
PG_HBA="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"

# Backup конфигурационных файлов
cp $PG_CONF "${PG_CONF}.backup"
cp $PG_HBA "${PG_HBA}.backup"

# Изменение postgresql.conf
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" $PG_CONF
sed -i "s/#port = 5432/port = $DB_PORT/" $PG_CONF

# Изменение pg_hba.conf
cat > $PG_HBA << EOL
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all            postgres                                peer
local   all            all                                     peer
host    all            all             127.0.0.1/32            md5
host    all            all             ::1/128                 md5
host    all            all             0.0.0.0/0               md5
EOL

# Запуск PostgreSQL
systemctl start postgresql
systemctl enable postgresql

# Создание пользователя и базы данных
echo -e "${GREEN}Creating database and user...${NC}"
sudo -u postgres psql << EOF
CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
CREATE DATABASE $DB_NAME OWNER $DB_USER;
ALTER USER $DB_USER WITH SUPERUSER;
EOF

# Настройка файрвола
echo -e "${GREEN}Configuring firewall...${NC}"
if command -v ufw >/dev/null; then
    ufw allow $DB_PORT/tcp
    ufw status
fi

# Проверка статуса PostgreSQL
systemctl status postgresql --no-pager

echo -e "${GREEN}Installation completed!${NC}"
echo -e "${BLUE}PostgreSQL is running on port: ${DB_PORT}${NC}"
echo -e "${BLUE}Database name: ${DB_NAME}${NC}"
echo -e "${BLUE}Database user: ${DB_USER}${NC}"
echo "You can now connect to your database using:"
echo "psql -h localhost -p $DB_PORT -U $DB_USER -d $DB_NAME"
echo
echo "For remote connections use:"
echo "psql -h YOUR_SERVER_IP -p $DB_PORT -U $DB_USER -d $DB_NAME"
echo
echo -e "${RED}Important: Make sure to save these credentials in a secure place!${NC}"

# Проверка подключения
echo -e "${GREEN}Testing connection...${NC}"
PGPASSWORD=$DB_PASS psql -h localhost -p $DB_PORT -U $DB_USER -d $DB_NAME -c "\conninfo"

# Добавление информации о создании бэкапов
echo
echo "To create a backup, use:"
echo "pg_dump -h localhost -p $DB_PORT -U $DB_USER -d $DB_NAME > backup.sql"
echo
echo "To restore from backup, use:"
echo "psql -h localhost -p $DB_PORT -U $DB_USER -d $DB_NAME < backup.sql"