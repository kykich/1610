#!/bin/sh
# Настройка фаервола (ufw) для RTK-чата на VPS.
#
# Что делает:
#   1) ставит ufw (если не установлен);
#   2) разрешает SSH-порт и публичный HTTPS-порт (8443);
#   3) ставит политику по умолчанию: входящие — deny, исходящие — allow;
#   4) включает ufw.
#
# ВАЖНО про SSH: правило для SSH-порта добавляется ДО включения ufw,
# иначе можно отрезать себе доступ к серверу.
#
# Переменные (можно переопределить):
#   RTK_SSH_PORT   — порт SSH (по умолчанию 22)
#   RTK_HTTPS_PORT — публичный HTTPS-порт nginx (по умолчанию 8443)
#
# Запуск на VPS (от root): sudo sh deploy/setup-firewall.sh
set -e

SSH_PORT="${RTK_SSH_PORT:-22}"
HTTPS_PORT="${RTK_HTTPS_PORT:-8443}"

echo "=== Параметры ==="
echo "SSH-порт:   $SSH_PORT"
echo "HTTPS-порт: $HTTPS_PORT"

echo "=== 1. Установка ufw (если нужно) ==="
if ! command -v ufw >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ufw
else
  echo "ufw уже установлен."
fi

echo "=== 2. Политики по умолчанию ==="
ufw default deny incoming
ufw default allow outgoing

echo "=== 3. Разрешения (SSH — СТРОГО до enable) ==="
ufw allow "${SSH_PORT}/tcp" comment 'SSH'
ufw allow "${HTTPS_PORT}/tcp" comment 'RTK HTTPS'

echo "=== 4. Включение ufw ==="
# --force не используем: если ufw уже активен, повторный enable безопасен.
ufw --force enable

echo "=== 5. Итог ==="
ufw status verbose
echo "=== Готово. Проверь доступ по SSH и https://<IP>:$HTTPS_PORT/ ПЕРЕД закрытием сессии. ==="
