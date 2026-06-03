#!/bin/bash
# TecnoTools — Postgres yedekten geri yükleme
#
# Kullanım:
#   ./scripts/restore-db.sh backups/db_20260514_030000.sql.gz
#   ./scripts/restore-db.sh s3://tecnotools-backups/db_20260514_030000.sql.gz

set -euo pipefail

if [[ -z "${1:-}" ]]; then
    echo "Kullanım: $0 <yedek-dosyası>" >&2
    exit 1
fi

SRC="$1"
PROJECT_DIR="${PROJECT_DIR:-/home/deploy/tecnotools/backend}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/docker-compose.prod.yml}"
DB_USER="${POSTGRES_USER:-tecnotools}"
DB_NAME="${POSTGRES_DB:-tecnotools}"

cd "$PROJECT_DIR"

echo "⚠️  DİKKAT: Mevcut DB üzerine yazılacak ($DB_NAME)."
read -rp "Devam etmek için 'EVET' yazın: " confirm
if [[ "$confirm" != "EVET" ]]; then
    echo "İptal edildi."
    exit 0
fi

TMP="/tmp/restore_$(date +%s).sql.gz"
if [[ "$SRC" =~ ^s3:// ]]; then
    AWS_ARGS=()
    if [[ -n "${AWS_ENDPOINT_URL:-}" ]]; then AWS_ARGS+=(--endpoint-url "$AWS_ENDPOINT_URL"); fi
    aws s3 cp "${AWS_ARGS[@]}" "$SRC" "$TMP"
    SRC="$TMP"
fi

echo "Geri yükleniyor: $SRC"
gunzip -c "$SRC" | docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DB_USER" "$DB_NAME"
echo "✓ Geri yükleme tamamlandı"

[[ -f "$TMP" ]] && rm -f "$TMP"
