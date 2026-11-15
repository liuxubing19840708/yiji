#!/usr/bin/env bash
# 扫菲每日备份：同一 PG 快照 pg_dump -Fc + 行数清单，另打包 media，保留 14 天。
# 异地上传（可选）：.env 里 OSS_UPLOAD=1 + 本机已配置 ossutil。
# 由 saofei-backup.timer 每日 02:30 触发（systemd journal 可查输出）。
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/saofei}
KEEP_DAYS=${KEEP_DAYS:-14}
STAMP=${STAMP:-$(date +%Y%m%d-%H%M%S)}
APP_DIR=${APP_DIR:-/opt/saofei/backend}
MEDIA_DIR=${MEDIA_DIR:-$APP_DIR/media}
PYTHON_BIN=${PYTHON_BIN:-$APP_DIR/.venv/bin/python}
PG_DUMP_BIN=${PG_DUMP_BIN:-pg_dump}

mkdir -p "$BACKUP_DIR"

# 环境变量优先，便于隔离演练；未提供时才从生产 .env 读取。
if [ -z "${DB_NAME+x}" ]; then
  DB_NAME=$(grep -E '^DB_NAME=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || true)
fi
if [ -z "${DB_USER+x}" ]; then
  DB_USER=$(grep -E '^DB_USER=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || true)
fi
if [ -z "${DB_HOST+x}" ]; then
  DB_HOST=$(grep -E '^DB_HOST=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || true)
fi
if [ -z "${DB_PORT+x}" ]; then
  DB_PORT=$(grep -E '^DB_PORT=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || true)
fi
if [ -z "${DB_PASSWORD+x}" ]; then
  DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || true)
fi
DB_NAME=${DB_NAME:-saofei}; DB_USER=${DB_USER:-saofei}
DB_HOST=${DB_HOST:-127.0.0.1}; DB_PORT=${DB_PORT:-5432}
export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGDATABASE="$DB_NAME" PGUSER="$DB_USER"
export PGPASSWORD="$DB_PASSWORD"

DUMP="$BACKUP_DIR/db-$STAMP.dump"
MANIFEST="$BACKUP_DIR/db-$STAMP.counts.json"
echo "[$(date '+%F %T')] backup start → $BACKUP_DIR"
"$PYTHON_BIN" "$APP_DIR/deploy/db_drill.py" backup \
  --dump "$DUMP" --manifest "$MANIFEST" --pg-dump "$PG_DUMP_BIN"

if [ -d "$MEDIA_DIR" ]; then
  tar --force-local -czf "$BACKUP_DIR/media-$STAMP.tar.gz" \
    -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
fi

# 保留 14 天；清单必须与数据库备份同生命周期。
find "$BACKUP_DIR" -name '*.dump' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name '*.counts.json' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name '*.tar.gz' -mtime +"$KEEP_DAYS" -delete

# 异地钩子（可选）：恢复清单和数据库备份一起上传，不能只传 dump。
if [ "${OSS_UPLOAD:-0}" = "1" ] && command -v ossutil >/dev/null 2>&1; then
  ossutil cp -f "$DUMP" "${OSS_BUCKET:-oss://saofei-backup}/db/" \
    && ossutil cp -f "$MANIFEST" "${OSS_BUCKET:-oss://saofei-backup}/db/" \
    && { [ ! -f "$BACKUP_DIR/media-$STAMP.tar.gz" ] \
         || ossutil cp -f "$BACKUP_DIR/media-$STAMP.tar.gz" \
            "${OSS_BUCKET:-oss://saofei-backup}/media/"; } \
    && echo "[$(date '+%F %T')] uploaded to OSS" \
    || echo "[$(date '+%F %T')] WARN: OSS upload failed"
fi

echo "[$(date '+%F %T')] backup done: db-$STAMP.dump + db-$STAMP.counts.json"
