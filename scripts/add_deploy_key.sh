#!/usr/bin/env bash
# Один раз кладёт ключ деплоя на сервер — дальше деплой идёт без пароля.
#
#   bash scripts/add_deploy_key.sh            # адрес из .env.deploy
#   bash scripts/add_deploy_key.sh 1.2.3.4    # или явно
#
# Пароль root вводит человек в этом окне — скрипт его не видит и не хранит.
# Лишних попыток не делайте: хостинги временно блокируют SSH после серии
# неудачных входов.
set -uo pipefail

cd "$(dirname "$0")/.."
if [ -f .env.deploy ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.deploy
  set +a
fi

HOST="${1:-${DEPLOY_HOST:-}}"
if [ -z "$HOST" ]; then
  read -r -p "IP сервера: " HOST
fi
KEY="${DEPLOY_KEY:-$HOME/.ssh/bot_calendar_deploy}"
KEY="${KEY/#\~/$HOME}"

if [ ! -f "$KEY" ]; then
  echo "==> Ключа нет — создаю $KEY (без пароля)"
  mkdir -p "$(dirname "$KEY")"
  ssh-keygen -q -t ed25519 -N "" -C "bot-calendar-deploy" -f "$KEY" || exit 1
fi

echo "==> Кладу ключ на сервер $HOST"
echo "    Сейчас спросит пароль root. Символы не отображаются — наберите и нажмите Enter."
echo

# Сразу к паролю: иначе ssh сначала перебирает личные ключи и спрашивает их пароли.
# На сервере ключ дописывается, только если его там ещё нет.
if ! tr -d '\r' < "$KEY.pub" | ssh \
    -o StrictHostKeyChecking=accept-new \
    -o PubkeyAuthentication=no \
    -o PreferredAuthentications=password,keyboard-interactive \
    -o ConnectTimeout=20 \
    "root@$HOST" \
    'k=$(cat); mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && { grep -qxF "$k" ~/.ssh/authorized_keys || echo "$k" >> ~/.ssh/authorized_keys; } && chmod 600 ~/.ssh/authorized_keys'
then
  echo
  echo "Не удалось положить ключ: неверный пароль или сервер недоступен." >&2
  echo "Проверьте пароль и попробуйте ещё раз — но не больше пары раз подряд." >&2
  exit 2
fi

echo
echo "==> Проверяю вход по ключу без пароля"
if ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 \
    "root@$HOST" "echo ok" >/dev/null 2>&1; then
  echo "Ключ работает: root@$HOST пускает без пароля."
  exit 0
fi

echo "Ключ записан, но вход без пароля не проходит — напишите разработчику." >&2
exit 3
