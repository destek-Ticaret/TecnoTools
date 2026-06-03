#!/bin/bash
# TecnoTools — Postgres yedek + S3 sync
#
# Çalıştırma (cron):
#   0 3 * * * /home/deploy/tecnotools/backend/scripts/backup-db.sh >> /var/log/tecnotools-backup.log 2>&1
#
# Gereken env (cron için /etc/environment veya scriptin başında set edin):
#   COMPOSE_FILE      = docker-compose.prod.yml yolu
#   POSTGRES_USER     = db kullanıcısı
#   POSTGRES_DB       = db adı
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
#   S3_BACKUP_BUCKET  = s3://tecnotools-backups (veya farklı sağlayıcı endpoint'i için AWS_ENDPOINT_URL)
#
# Retention:
#   - Local:  14 gün
#   - S3:     90 gün (S3 lifecycle policy daha güvenli; bu script sadece upload yapar)

set -euo pipefail

# ── Ayarlar ──
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
PROJECT_DIR="${PROJECT_DIR:-/home/deploy/tecnotools/backend}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/docker-compose.prod.yml}"
BACKUP_DIR="$PROJECT_DIR/backups"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-14}"
DB_USER="${POSTGRES_USER:-tecnotools}"
DB_NAME="${POSTGRES_DB:-tecnotools}"

mkdir -p "$BACKUP_DIR"

DUMP_FILE="$BACKUP_DIR/db_${TIMESTAMP}.sql.gz"

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Yedek başlıyor: $DUMP_FILE"

# ── 1) pg_dump (gzip ile) ──
cd "$PROJECT_DIR"
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "$DB_USER" --no-owner --clean --if-exists "$DB_NAME" \
    | gzip -9 > "$DUMP_FILE"

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "  ✓ pg_dump tamamlandı ($SIZE)"

# ── 2) Bütünlük testi (gzip header) ──
if ! gzip -t "$DUMP_FILE" 2>/dev/null; then
    echo "  ✗ HATA: Yedek dosyası bozuk!" >&2
    rm -f "$DUMP_FILE"
    exit 1
fi
echo "  ✓ Gzip bütünlük OK"

# ── 3) S3 upload (aws cli) ──
if [[ -n "${S3_BACKUP_BUCKET:-}" ]] && command -v aws >/dev/null 2>&1; then
    AWS_ARGS=()
    if [[ -n "${AWS_ENDPOINT_URL:-}" ]]; then
        AWS_ARGS+=(--endpoint-url "$AWS_ENDPOINT_URL")
    fi
    aws s3 cp "${AWS_ARGS[@]}" "$DUMP_FILE" "$S3_BACKUP_BUCKET/db_${TIMESTAMP}.sql.gz" --storage-class STANDARD_IA
    echo "  ✓ S3 upload tamam"
else
    echo "  · S3 atlandı (S3_BACKUP_BUCKET veya aws cli yok)"
fi

# ── 4) Local retention (eski yedekleri sil) ──
find "$BACKUP_DIR" -name "db_*.sql.gz" -mtime "+${LOCAL_RETENTION_DAYS}" -delete -print | while read -r f; do
    echo "  · Silindi (>$LOCAL_RETENTION_DAYS gün): $f"
done

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Yedek tamamlandı."
