#!/bin/bash

# Цвета для вывода
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}3x-ui Installation Script by izzzzzi${NC}"
echo "----------------------------------------"

# Запрос данных у пользователя
read -p "Enter your domain (e.g., example.com): " DOMAIN
read -p "Enter desired admin panel port (default: 2053): " PANEL_PORT
read -p "Enter desired sub panel port (default: 2054): " SUB_PANEL_PORT

# Использование значения по умолчанию для порта, если не указано
PANEL_PORT=${PANEL_PORT:-2053}
SUB_PANEL_PORT=${SUB_PANEL_PORT:-2054}

# Создание необходимых директорий
echo -e "${GREEN}Creating directories...${NC}"
mkdir -p nginx/conf.d db cert

# Установка certbot
echo -e "${GREEN}Installing certbot...${NC}"
apt-get update
apt-get install certbot -y

# Остановка nginx если он запущен
echo -e "${GREEN}Stopping nginx if running...${NC}"
docker-compose down 2>/dev/null
systemctl stop nginx 2>/dev/null

# Получение сертификата
echo -e "${GREEN}Obtaining SSL certificate...${NC}"
certbot certonly --standalone --agree-tos --register-unsafely-without-email -d $DOMAIN

# Проверка успешности получения сертификата
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "Failed to obtain SSL certificate. Please check your domain settings."
    exit 1
fi

# Тестирование автообновления
echo -e "${GREEN}Testing certificate renewal...${NC}"
certbot renew --dry-run

# Создание docker-compose.yml
echo -e "${GREEN}Creating docker-compose.yml...${NC}"
cat > docker-compose.yml << EOL
version: "3"

services:
  nginx:
    image: nginx:alpine
    container_name: nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/conf.d:/etc/nginx/conf.d
      - /etc/letsencrypt:/etc/letsencrypt
      - /var/lib/letsencrypt:/var/lib/letsencrypt
    networks:
      - proxy-network
    restart: unless-stopped
    depends_on:
      - 3x-ui

  3x-ui:
    image: ghcr.io/mhsanaei/3x-ui:latest
    container_name: 3x-ui
    hostname: ${DOMAIN}
    volumes:
      - \$PWD/db/:/etc/x-ui/
      - \$PWD/cert/:/root/cert/
      - /etc/letsencrypt:/etc/letsencrypt:ro
    environment:
      XRAY_VMESS_AEAD_FORCED: "false"
    tty: true
    networks:
      - proxy-network
    restart: unless-stopped

networks:
  proxy-network:
    driver: bridge
EOL

# Создание конфигурации Nginx
echo -e "${GREEN}Creating Nginx configuration...${NC}"
cat > nginx/conf.d/default.conf << EOL
server {
    listen 80;
    server_name ${DOMAIN};
    
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;

    location / {
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header Range \$http_range;
        proxy_set_header If-Range \$http_if_range; 
        proxy_redirect off;
        proxy_pass http://3x-ui:${PANEL_PORT};
    }

    location /sub {
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header Range \$http_range;
        proxy_set_header If-Range \$http_if_range; 
        proxy_redirect off;
        proxy_pass http://3x-ui:${SUB_PANEL_PORT};
    }
}
EOL

# Проверка наличия Docker и Docker Compose
if ! command -v docker &> /dev/null || ! command -v docker-compose &> /dev/null; then
    echo -e "${GREEN}Installing Docker and Docker Compose...${NC}"
    curl -fsSL https://get.docker.com | sh
    curl -L "https://github.com/docker/compose/releases/download/v2.12.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

# Настройка автообновления сертификатов
echo -e "${GREEN}Setting up certificate auto-renewal...${NC}"
cat > /etc/cron.d/certbot-renew << EOL
0 */12 * * * root certbot renew --quiet --deploy-hook "docker-compose -f $(pwd)/docker-compose.yml restart nginx"
EOL

# Запуск сервисов
echo -e "${GREEN}Starting all services...${NC}"
docker-compose up -d

echo -e "${GREEN}Installation completed!${NC}"
echo -e "${BLUE}You can access your 3x-ui panel at: https://${DOMAIN}${NC}"
echo -e "${YELLOW}Important: Default access to 3x-ui panel - ${NC}"
echo -e "${YELLOW}Login: admin${NC}"
echo -e "${YELLOW}Password: admin${NC}"
echo -e "${YELLOW}For security, it is recommended to change the password at: https://${DOMAIN}/panel/settings${NC}"
echo "Please wait a few minutes for all services to start properly."
echo "Default credentials can be found in the 3x-ui documentation."

EOL