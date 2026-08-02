#!/usr/bin/env bash
# Бэкап Postgres: pg_dump из контейнера → gzip в backups/ (ротация 30 дней).
# Если в /opt/finance/.env заданы S3_ENDPOINT/S3_BUCKET и ключи AWS_*,
# копия дополнительно уезжает в S3 (Timeweb). Запуск кроном раз в сутки,
# установка крона — docs/deploy.md.
set -euo pipefail
cd /opt/finance

stamp=$(date +%Y-%m-%d-%H%M)
file="backups/finance-$stamp.sql.gz"
mkdir -p backups

docker compose exec -T postgres pg_dump -U finance finance | gzip > "$file"

find backups -name 'finance-*.sql.gz' -mtime +30 -delete

s3_endpoint=$(grep '^S3_ENDPOINT=' .env | cut -d= -f2- || true)
s3_bucket=$(grep '^S3_BUCKET=' .env | cut -d= -f2- || true)
if [ -n "$s3_bucket" ] && [ -n "$s3_endpoint" ]; then
  docker run --rm --env-file .env -v /opt/finance/backups:/backups \
    amazon/aws-cli --endpoint-url "$s3_endpoint" \
    s3 cp "/$file" "s3://$s3_bucket/$(basename "$file")"
fi

echo "OK: $file"
