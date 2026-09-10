#!/usr/bin/env bash
# Первичная подготовка чистого Ubuntu 24.04 под BOT_CALENDAR. Один раз, от root:
#
#   ssh root@IP 'bash -s' -- calendar.1-2-3-4.nip.io < scripts/server_bootstrap.sh
#
# Скрипт не выпускает сертификат: certbot требует принять соглашение Let's Encrypt,
# а это решение владельца сервера. Команда certbot — в docs/INSTALL.md.
# Повторный запуск безопасен: каждый шаг проверяет, не сделан ли он уже.
set -euo pipefail

DOMAIN="${1:?укажите домен первым аргументом}"
REPO_URL="${2:-https://github.com/ivanovich1071/BOT_CALENDAR.git}"
APP_DIR=/opt/bot-calendar

export DEBIAN_FRONTEND=noninteractive

echo "==> Пакеты Ubuntu (без curl | sh)"
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 nginx certbot python3-certbot-nginx ufw git curl

echo "==> Swap 2 ГБ: при 1 ГБ RAM без него сборка образа и PostgreSQL рискуют упереться в OOM"
# Хостинг может заранее создать маленький /swapfile — смотрим на размер, а не на наличие
if [ "$(free -m | awk '/^Swap:/{print $2}')" -lt 1500 ]; then
  if swapon --show=NAME --noheadings | grep -qx /swapfile; then
    swapoff /swapfile
    rm -f /swapfile
  fi
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -q -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

echo "==> Файрвол: только SSH, HTTP и HTTPS"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

echo "==> Docker"
systemctl enable --now docker >/dev/null

echo "==> Код в $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull -q --ff-only
else
  git clone -q "$REPO_URL" "$APP_DIR"
fi

echo "==> nginx для $DOMAIN"
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/bot-calendar
ln -sf /etc/nginx/sites-available/bot-calendar /etc/nginx/sites-enabled/bot-calendar
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx

echo "==> Готово. Дальше: $APP_DIR/.env, сертификат, docker compose up"
free -m | head -3
df -h / | tail -1
