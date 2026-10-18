#!/bin/sh
# Установка и настройка fail2ban для RTK-чата (защита SSH от брутфорса).
#
# Что делает:
#   1) ставит fail2ban (если не установлен);
#   2) кладёт jail.local с защитой sshd (бан после нескольких неудач);
#   3) включает и запускает fail2ban.
#
# fail2ban использует backend=systemd (читает journald) — отдельные логи не нужны.
#
# Переменные (можно переопределить):
#   RTK_SSH_PORT — порт SSH, который защищаем (по умолчанию 22)
#
# Запуск на VPS (от root): sudo sh deploy/setup-fail2ban.sh
set -e

SSH_PORT="${RTK_SSH_PORT:-22}"
JAIL=/etc/fail2ban/jail.local

echo "=== Параметры ==="
echo "SSH-порт: $SSH_PORT"

echo "=== 1. Установка fail2ban (если нужно) ==="
if ! command -v fail2ban-server >/dev/null 2>&1; then
  apt-get update
  apt-get install -y fail2ban
else
  echo "fail2ban уже установлен."
fi

echo "=== 2. Конфиг jail.local ==="
cat > "$JAIL" <<EOF
# Управляется deploy/setup-fail2ban.sh — правки вручную будут перезаписаны.
[DEFAULT]
# Читать логи через journald (без отдельных файлов).
backend = systemd

[sshd]
enabled  = true
port     = $SSH_PORT
# Мягкий, но действенный порог: 5 неудач -> бан на 1 час.
maxretry = 5
findtime = 10m
bantime  = 1h
EOF

echo "=== 3. Включение и запуск ==="
systemctl enable fail2ban
systemctl restart fail2ban

echo "=== 4. Итог ==="
sleep 2
systemctl is-active fail2ban
fail2ban-client status sshd || true
echo "=== Готово ==="
