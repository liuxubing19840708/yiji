#!/usr/bin/env bash
# 恢复演练：校验 dump 与备份时点清单 → 恢复临时库 → 全表行数对账 → 清理。
# 不读取或比较活跃源库，因此旧备份不会受备份后的业务写入影响。
# 用法：bash restore_drill.sh <备份.dump> [备份.counts.json] [媒体.tar.gz]
set -euo pipefail

DUMP=${1:?用法: restore_drill.sh <备份.dump 路径> [备份.counts.json 路径] [媒体.tar.gz]}
MANIFEST=${2:-${DUMP%.dump}.counts.json}
MEDIA_ARCHIVE=${3:-${MEDIA_ARCHIVE:-}}
APP_DIR=${APP_DIR:-/opt/saofei/backend}
PYTHON_BIN=${PYTHON_BIN:-$APP_DIR/.venv/bin/python}
PG_RESTORE_BIN=${PG_RESTORE_BIN:-pg_restore}

# 环境变量优先，便于隔离演练；未提供时才从生产 .env 读取。
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
DB_USER=${DB_USER:-saofei}; DB_HOST=${DB_HOST:-127.0.0.1}; DB_PORT=${DB_PORT:-5432}
export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$DB_USER" PGPASSWORD="$DB_PASSWORD"

[ -f "$DUMP" ] || { echo "备份文件不存在：$DUMP" >&2; exit 1; }
[ -f "$MANIFEST" ] || { echo "备份清单不存在：$MANIFEST（请与 dump 一起取回）" >&2; exit 1; }
"$PYTHON_BIN" "$APP_DIR/deploy/db_drill.py" check-dump --dump "$DUMP" --manifest "$MANIFEST"

DRILL_DB=${DRILL_DB:-saofei_drill_$$}
cleanup() {
  if [ "${KEEP_DRILL_DB:-0}" != "1" ]; then
    PGDATABASE=postgres "$PYTHON_BIN" "$APP_DIR/deploy/db_drill.py" drop-db \
      --name "$DRILL_DB" >/dev/null || true
  fi
}
trap cleanup EXIT

echo "== 演练库 $DRILL_DB =="
PGDATABASE=postgres "$PYTHON_BIN" "$APP_DIR/deploy/db_drill.py" create-db \
  --name "$DRILL_DB" --owner "$DB_USER"
"$PG_RESTORE_BIN" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DRILL_DB" \
  --no-owner --no-privileges --exit-on-error "$DUMP"

echo "== 行数对账（备份时点清单 vs 演练库）=="
PGDATABASE="$DRILL_DB" "$PYTHON_BIN" "$APP_DIR/deploy/db_drill.py" verify \
  --manifest "$MANIFEST"
if [ -n "$MEDIA_ARCHIVE" ]; then
  [ -f "$MEDIA_ARCHIVE" ] || { echo "媒体备份不存在：$MEDIA_ARCHIVE" >&2; exit 1; }
  "$PYTHON_BIN" "$APP_DIR/deploy/db_drill.py" media-drill --archive "$MEDIA_ARCHIVE"
fi
echo "== 演练通过：$DUMP 恢复完整 =="
