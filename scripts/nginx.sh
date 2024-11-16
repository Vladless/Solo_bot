#!/bin/bash

# Цвета для вывода
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}Nginx Installation and Setup Script by izzzzzi${NC}"
echo "----------------------------------------"

# Проверка root прав
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root${NC}" 
   exit 1
fi

# Запрос данных у пользователя
read -p "Enter your domain name: " DOMAIN
read -p "Enter HTTP port (default: 80): " HTTP_PORT
read -p "Enter proxy pass port (default: 3001): " PROXY_PORT

# Использование значений по умолчанию
HTTP_PORT=${HTTP_PORT:-80}
PROXY_PORT=${PROXY_PORT:-3001}

# Проверка занятости портов
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null ; then
        echo -e "${RED}Port $1 is already in use!${NC}"
        exit 1
    fi
}

echo -e "${GREEN}Checking ports availability...${NC}"
check_port $HTTP_PORT

# Установка необходимых пакетов
echo -e "${GREEN}Installing required packages...${NC}"
apt-get update
apt-get install nginx certbot python3-certbot-nginx wget openssl -y

# Создание директории для статического сайта и валидации сертификата
echo -e "${GREEN}Creating directories...${NC}"
mkdir -p /var/www/certbot
mkdir -p /var/www/mywebsite

# Создание HTTP-конфигурации для Nginx
echo -e "${GREEN}Creating initial Nginx configuration...${NC}"
cat > /etc/nginx/sites-available/$DOMAIN << EOF
server {
    listen $HTTP_PORT;
    server_name $DOMAIN;

    location / {
        return 301 https://\$host\$request_uri;
    }

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
}
EOF

ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Проверка конфигурации и перезапуск Nginx
echo -e "${GREEN}Testing Nginx configuration...${NC}"
nginx -t
if [ $? -ne 0 ]; then
    echo -e "${RED}Error in Nginx configuration. Please check the logs.${NC}"
    exit 1
fi
systemctl reload nginx

# Получение сертификата
echo -e "${GREEN}Obtaining SSL certificate...${NC}"
certbot certonly --webroot --webroot-path /var/www/certbot -d $DOMAIN --agree-tos --no-eff-email --register-unsafely-without-email
if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to obtain SSL certificate. Please check certbot logs.${NC}"
    exit 1
fi

# Установка недостающих файлов для SSL
echo -e "${GREEN}Ensuring required SSL files exist...${NC}"
if [ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]; then
    wget -O /etc/letsencrypt/options-ssl-nginx.conf https://raw.githubusercontent.com/certbot/certbot/main/certbot_nginx/certbot_nginx/options-ssl-nginx.conf
fi
if [ ! -f /etc/letsencrypt/ssl-dhparams.pem ]; then
    openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
fi

# Обновление конфигурации для HTTPS
echo -e "${GREEN}Creating HTTPS configuration...${NC}"
cat > /etc/nginx/sites-available/$DOMAIN << EOF
server {
    listen $HTTP_PORT;
    server_name $DOMAIN;

    location / {
        return 301 https://\$host\$request_uri;
    }

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
}

server {
    listen 443 ssl;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/mywebsite;
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;
    }

    location /webhook {
        proxy_pass http://localhost:$PROXY_PORT/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /sub/ {
        proxy_pass http://localhost:$PROXY_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        add_header Content-Type text/plain;
        add_header Content-Disposition inline;
        add_header Cache-Control no-store;
        add_header Pragma no-cache;
    }
    
    location /yookassa/webhook {
        proxy_pass http://localhost:$PROXY_PORT/yookassa/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /freekassa/webhook {
        proxy_pass http://localhost:$PROXY_PORT/freekassa/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /cryptobot/webhook {
        proxy_pass http://localhost:$PROXY_PORT/cryptobot/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /robokassa/webhook {
        proxy_pass http://localhost:$PROXY_PORT/robokassa/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF

# Проверка конфигурации и перезапуск Nginx
echo -e "${GREEN}Testing Nginx configuration...${NC}"
nginx -t
if [ $? -ne 0 ]; then
    echo -e "${RED}Error in updated Nginx configuration. Please check the logs.${NC}"
    exit 1
fi
systemctl reload nginx

# Настройка автообновления сертификата
echo -e "${GREEN}Setting up certificate auto-renewal...${NC}"
(crontab -l 2>/dev/null; echo "0 12 * * * /usr/bin/certbot renew --quiet && systemctl reload nginx") | crontab -

# Завершение
echo -e "${GREEN}Setup completed!${NC}"
echo -e "${BLUE}Domain: ${DOMAIN}${NC}"
echo -e "${BLUE}HTTP port: ${HTTP_PORT}${NC}"
echo -e "${BLUE}Proxy pass port: ${PROXY_PORT}${NC}"
echo
echo -e "${RED}Important: Ensure DNS points to this server's IP!${NC}"
