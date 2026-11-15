#!/usr/bin/env bash
# 扫菲首次上机 bootstrap（Ubuntu 22.04，root 或 sudo 执行，交互式确认）。
# 前提：域名已解析到本机、云安全组放行 80/443、代码将克隆到 /opt/saofei。
set -euo pipefail

REPO_URL=${REPO_URL:-<你的仓库地址>}
APP_ROOT=/opt/saofei
DB_NAME=saofei
DB_USER=saofei
DOMAIN=${DOMAIN:-api.yourdomain.com}

echo "== 将部署 $DOMAIN → $APP_ROOT，继续？(回车继续 / Ctrl+C 取消) =="
read -r _

echo "== [1/8] 系统依赖 =="
apt update -qq
apt install -y python3-venv python3-pip libpq-dev nginx postgresql postgresql-contrib git curl

echo "== [2/8] 服务账号 =="
id -u saofei &>/dev/null || useradd -r -m -d /opt/saofei -s /usr/sbin/nologin saofei

echo "== [3/8] PostgreSQL 建库建号 =="
su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'\" | grep -q 1 || psql -c \"CREATE USER $DB_USER WITH PASSWORD 'CHANGEME';\""
su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$DB_NAME'\" | grep -q 1 || psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_USER;\""

echo "== [4/8] 代码 + venv =="
[ -d "$APP_ROOT/backend" ] || git clone "$REPO_URL" "$APP_ROOT"
cd "$APP_ROOT/backend"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== [5/8] .env =="
if [ ! -f .env ]; then
  cp deploy/.env.production.template .env
  SECRET=$(.venv/bin/python -c "import secrets;print(secrets.token_urlsafe(50))")
  DBPASS=$(.venv/bin/python -c "import secrets;print(secrets.token_urlsafe(24))")
  sed -i "s|^SECRET_KEY=$|SECRET_KEY=$SECRET|" .env
  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$DBPASS|" .env
  sed -i "s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=$DOMAIN|" .env
  chown saofei:saofei .env
  echo "   → .env 已生成（SECRET_KEY/DB_PASSWORD 已随机），ALLOWED_HOSTS=$DOMAIN；DB 密码需与 [3/8] 的 CHANGEME 同步改："
  echo "     su - postgres -c \"psql -c \\\"ALTER USER $DB_USER WITH PASSWORD '$DBPASS';\\\"\"（已代改）"
  su - postgres -c "psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DBPASS';\""
fi

echo "== [6/8] 迁移 + 静态 + 日志目录 =="
mkdir -p /var/log/saofei /var/backups/saofei
chown saofei:saofei /var/log/saofei /var/backups/saofei
sudo -u saofei .venv/bin/python manage.py migrate --noinput
sudo -u saofei .venv/bin/python manage.py collectstatic --noinput

echo "== [7/8] systemd + nginx =="
cp deploy/saofei.service /etc/systemd/system/
cp deploy/saofei-backup.service deploy/saofei-backup.timer /etc/systemd/system/
sed -i "s|/opt/saofei/backend|$APP_ROOT/backend|g" /etc/systemd/system/saofei*.service   # 路径与 APP_ROOT 一致
cp deploy/nginx-saofei.conf /etc/nginx/sites-available/saofei
sed -i "s|api.yourdomain.com|$DOMAIN|g" /etc/nginx/sites-available/saofei
ln -sf /etc/nginx/sites-available/saofei /etc/nginx/sites-enabled/saofei
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now saofei.service saofei-backup.timer
systemctl reload nginx

echo "== [8/8] 证书 =="
apt install -y certbot python3-certbot-nginx
certbot --nginx -d "$DOMAIN"
echo "== bootstrap 完成。验证：curl https://$DOMAIN/healthz 应返回 {\"status\":\"ok\"} =="
