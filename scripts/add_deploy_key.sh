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

# Понятная причина по тексту ошибки ssh
explain_ssh_error() {
  case "$1" in
    *"Permission denied"*)
      echo "Сервер отклонил пароль. Частые причины:"
      echo "  • русская раскладка или Caps Lock при вводе;"
      echo "  • пароль скопирован с пробелом или устарел — возьмите актуальный в панели хостинга;"
      echo "  • на сервере запрещён вход root по паролю через SSH. Проверка: войдите с этим"
      echo "    паролем в веб-консоль сервера в панели хостинга. Там пускает, а здесь нет —"
      echo "    напишите разработчику."
      echo "Не запускайте много раз подряд: после серии ошибок хостинг блокирует SSH."
      ;;
    *"kex_exchange_identification"* | *"Connection closed by"* | *"Connection reset"*)
      echo "Сервер закрыл соединение, не дойдя до пароля: хостинг временно заблокировал SSH"
      echo "для вашего IP после серии попыток. Подождите 15 минут, ничего не запуская,"
      echo "и повторите один раз. Или подключитесь через раздачу интернета с телефона —"
      echo "у неё другой IP."
      ;;
    *"timed out"* | *"No route to host"* | *"Connection refused"*)
      echo "Сервер не отвечает на порту 22. Проверьте в панели хостинга, что сервер включён."
      ;;
    *)
      echo "Не удалось положить ключ. Текст ошибки — выше."
      ;;
  esac
}

main() {
  cd "$(dirname "$0")/.." || exit 1
  if [ -f .env.deploy ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env.deploy
    set +a
  fi

  local host="${1:-${DEPLOY_HOST:-}}"
  if [ -z "$host" ]; then
    read -r -p "IP сервера: " host
  fi
  local key="${DEPLOY_KEY:-$HOME/.ssh/bot_calendar_deploy}"
  key="${key/#\~/$HOME}"

  if [ ! -f "$key" ]; then
    echo "==> Ключа нет — создаю $key (без пароля)"
    mkdir -p "$(dirname "$key")"
    ssh-keygen -q -t ed25519 -N "" -C "bot-calendar-deploy" -f "$key" || exit 1
  fi

  echo "==> Кладу ключ на сервер $host"
  echo "    Сейчас спросит пароль root. Перед вводом:"
  echo "      • раскладка английская (EN), Caps Lock выключен;"
  echo "      • вставлять лучше правой кнопкой мыши;"
  echo "      • символы не отображаются — это нормально, после ввода нажмите Enter."
  echo "    Сервер даёт три попытки."
  echo

  # Сразу к паролю: иначе ssh сначала перебирает личные ключи и спрашивает их пароли.
  # На сервере ключ дописывается, только если его там ещё нет.
  # Запрос пароля ssh пишет в терминал, а не в stderr, поэтому ошибки можно собрать в файл.
  local err
  err=$(mktemp)
  if ! tr -d '\r' < "$key.pub" | ssh \
      -o StrictHostKeyChecking=accept-new \
      -o PubkeyAuthentication=no \
      -o PreferredAuthentications=password,keyboard-interactive \
      -o ConnectTimeout=20 \
      "root@$host" \
      'k=$(cat); mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && { grep -qxF "$k" ~/.ssh/authorized_keys || echo "$k" >> ~/.ssh/authorized_keys; } && chmod 600 ~/.ssh/authorized_keys' \
      2>"$err"
  then
    echo
    cat "$err" >&2
    echo >&2
    explain_ssh_error "$(cat "$err")" >&2
    rm -f "$err"
    exit 2
  fi
  rm -f "$err"

  echo
  echo "==> Проверяю вход по ключу без пароля"
  if ssh -i "$key" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 \
      "root@$host" "echo ok" >/dev/null 2>&1; then
    echo "Ключ работает: root@$host пускает без пароля."
    exit 0
  fi

  echo "Ключ записан, но вход без пароля не проходит — напишите разработчику." >&2
  exit 3
}

# При подключении через source (для проверки) только объявляем функции
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
