#!/usr/bin/env bash
# SQLite → PostgreSQL 一次性数据迁移（首次上云执行；先演练后真迁）。
# 原理：dumpdata 排除框架表（natural keys 保证 contenttype 重映射）
#       → PG 侧 migrate 建空库 → loaddata → 逐表行数对账，全等才通过。
# 用法：  bash migrate_sqlite_to_pg.sh drill   # 导入到 saofei_drill 演练库（推荐先跑）
#         bash migrate_sqlite_to_pg.sh final   # 导入正式 saofei 库
set -euo pipefail

MODE=${1:?用法: migrate_sqlite_to_pg.sh drill|final}
APP_DIR=/opt/saofei/backend
PY=$APP_DIR/.venv/bin/python
STAMP=$(date +%Y%m%d-%H%M)

cd "$APP_DIR"

echo "== [1/5] SQLite 全量导出（排除框架表）=="
"$PY" manage.py dumpdata \
  --natural-foreign --natural-primary \
  -e contenttypes -e auth.permission -e sessions -e admin.logentry \
  -o "/tmp/saofei-data-$STAMP.json"
echo "导出: /tmp/saofei-data-$STAMP.json ($(du -h /tmp/saofei-data-$STAMP.json | cut -f1))"

if [ "$MODE" = "drill" ]; then
  TARGET_DB=saofei_drill
else
  TARGET_DB=${DB_NAME:-saofei}
fi

echo "== [2/5] 临时切换 .env 到 PostgreSQL 目标库 $TARGET_DB =="
cp .env ".env.bak-$STAMP"
sed -i "s/^DB_NAME=.*/DB_NAME=$TARGET_DB/" .env
trap 'mv ".env.bak-$STAMP" .env' EXIT

echo "== [3/5] PG 建表 =="
"$PY" manage.py migrate --noinput

echo "== [4/5] 导入数据 =="
"$PY" manage.py loaddata "/tmp/saofei-data-$STAMP.json"

echo "== [5/5] 行数对账（导出 JSON 计数 vs PG 实际行数，全部相等=迁移通过）=="
# 对账：dump JSON 里每模型计数 vs PG（此刻 default 已指向目标库）实际行数
"$PY" manage.py shell -c "
import json, collections
from django.apps import apps
from django.db import connections
data = json.load(open('/tmp/saofei-data-$STAMP.json', encoding='utf-8'))
expect = collections.Counter(item['model'] for item in data)
fail = 0
for model in apps.get_models():
    key = model._meta.label.lower().replace('.', '.')
    # dumpdata 的 model 键 = 'app.model' 小写
    k = f'{model._meta.app_label}.{model._meta.model_name}'
    if k not in expect:
        continue
    actual = model.objects.count()
    if actual != expect[k]:
        print(f'DIFF {k}: expect={expect[k]} actual={actual}')
        fail = 1
print('MIGRATION_OK' if fail == 0 else 'MIGRATION_DIFF')
" | tee "/tmp/migrate-check-$STAMP.log" | grep -E "MIGRATION_|DIFF" || true

if grep -q MIGRATION_OK "/tmp/migrate-check-$STAMP.log"; then
  echo "== 迁移对账通过 =="
else
  echo "== 存在行数差异，禁止切流；排查后重跑 =="
  exit 1
fi

echo "完成。drill 模式清理：psql -U \$DB_USER -d postgres -c 'DROP DATABASE saofei_drill;'"
