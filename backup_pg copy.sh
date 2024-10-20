# Параметры
DB_NAME="имя_вашей_базы"
USER="имя_пользователя_postgresql"
HOST="localhost"
BACKUP_DIR="/путь/к/папке/резервных_копий"
DATE=$(date +\%Y-\%m-\%d-\%H\%M\%S)
BACKUP_FILE="$BACKUP_DIR/$DB_NAME-backup-$DATE.sql"

# Команда для резервного копирования
pg_dump -U $USER -h $HOST -F c -f $BACKUP_FILE $DB_NAME

# Удаление старых копий (например, старше 7 дней)
find $BACKUP_DIR -type f -name "*.sql" -mtime +7 -exec rm {} \;