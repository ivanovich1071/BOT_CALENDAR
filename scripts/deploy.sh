#!/usr/bin/env bash
# Повторный деплой одной командой из Git Bash:
#
#   bash scripts/deploy.sh
#
# Адрес сервера — в .env.deploy (в git не попадает):
#   DEPLOY_HOST=1.2.3.4
#   DEPLOY_DOMAIN=calendar.1-2-3-4.nip.io
#   DEPLOY_KEY=~/.ssh/bot_calendar_deploy     # необязательно
#
# Всё на сервере делается в ОДНОЙ SSH-сессии: хостинги режут частые подключения к порту 22.
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .env.deploy ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.deploy
  set +a
fi
: "${DEPLOY_HOST:?задайте DEPLOY_HOST в .env.deploy}"
: "${DEPLOY_DOMAIN:?задайте DEPLOY_DOMAIN в .env.deploy}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/bot_calendar_deploy}"
KEY="${KEY/#\~/$HOME}"
APP_DIR="${DEPLOY_DIR:-/opt/bot-calendar}"

echo "==> Проверка: всё закоммичено и отправлено"
if [ -n "$(git status --porcelain)" ]; then
  echo "Есть незакоммиченные изменения — сначала коммит." >&2
  exit 1
fi
git fetch -q origin
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "Локальный main расходится с origin/main — сначала git push." >&2
  exit 1
fi
REV=$(git rev-parse --short HEAD)

echo "==> Сервер $DEPLOY_HOST: обновление до $REV и пересборка"
ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 \
  "root@$DEPLOY_HOST" bash -s -- "$APP_DIR" <<'REMOTE'
set -euo pipefail
cd "$1"
git fetch -q origin
git reset -q --hard origin/main
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
for _ in $(seq 1 40); do
  curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break
  sleep 3
done
docker compose -f docker-compose.prod.yml ps --format 'table {{.Service}}\t{{.Status}}'
echo "health: $(curl -fsS http://127.0.0.1:8000/health)"
docker image prune -f >/dev/null
REMOTE

echo "==> Снаружи по HTTPS"
curl -fsS "https://$DEPLOY_DOMAIN/health"
echo
echo "==> Готово: $REV"
