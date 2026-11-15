#!/usr/bin/env bash
# 日常升级：前置备份 → git pull → pip sync → migrate → collectstatic → restart → /healthz 断言。
# 失败不自动回滚代码（迁移可能不可逆）：打印回滚指引，前置备份保留待人工裁决。
set -euo pipefail

APP_DIR=/opt/saofei/backend
PY=$APP_DIR/.venv/bin/python
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8001/healthz}

echo "== [1/6] 前置备份 =="
bash "$APP_DIR/deploy/backup.sh"

echo "== [2/6] 拉代码 =="
cd "$APP_DIR"
git fetch --tags
git pull --ff-only

echo "== [3/6] 依赖同步 =="
"$PY" -m pip install -q -r requirements.txt

echo "== [4/6] 迁移（先核对无未生成迁移）=="
"$PY" manage.py makemigrations --check --dry-run
"$PY" manage.py migrate --noinput

echo "== [5/6] 静态收集 + 重启 =="
"$PY" manage.py collectstatic --noinput
systemctl restart saofei.service
sleep 3

echo "== [6/6] 健康断言 =="
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)
if [ "$HTTP" = "200" ]; then
  echo "== 升级完成，healthz=200 =="
else
  echo "!! healthz=$HTTP 非 200 —— 升级失败处理指引："
  echo "   1) journalctl -u saofei -n 100 看崩溃原因"
  echo "   2) 数据回滚：用前置备份 /var/backups/saofei/ 里最新 .dump"
  echo "      pg_restore -h 127.0.0.1 -U \$DB_USER -d \$DB_NAME --clean --if-exists <dump>"
  echo "   3) 代码回滚：git reflog 找上一个 tag 后 git checkout，再 systemctl restart saofei"
  exit 1
fi
