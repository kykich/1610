#!/bin/sh
# Настройка HTTPS для RTK-чата (самоподписанный сертификат, доступ по IP).
#
# Делает:
#   1) создаёт самоподписанный сертификат с SAN = публичный IP (если нет);
#   2) ставит nginx-конфиг из репозитория (HTTPS на НЕСТАНДАРТНОМ порту 8443,
#      порт 80 не слушается — меньше шума от сканеров типовых портов);
#   3) проверяет конфиг и перезагружает nginx.
#
# Запуск на VPS (от root): sudo sh deploy/setup-https.sh
set -e

# Публичный IP (для SAN сертификата). Можно переопределить: RTK_IP=1.2.3.4
IP="${RTK_IP:-91.188.213.252}"
CERT_DIR=/etc/ssl/rtk
CERT="$CERT_DIR/rtk.crt"
KEY="$CERT_DIR/rtk.key"

echo "=== 1. Сертификат ($CERT) ==="
mkdir -p "$CERT_DIR"
if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  echo "Сертификат уже существует — не пересоздаю."
else
  openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
    -keyout "$KEY" -out "$CERT" \
    -subj "/CN=$IP" \
    -addext "subjectAltName=IP:$IP"
  chmod 600 "$KEY"
  chmod 644 "$CERT"
  echo "Сертификат создан (CN=$IP, SAN=IP:$IP)."
fi
openssl x509 -in "$CERT" -noout -subject -ext subjectAltName || true

echo "=== 2. Конфиг nginx ==="
cp /opt/rtk/deploy/nginx.conf /etc/nginx/sites-available/rtk
# Симлинк на случай, если его нет.
[ -e /etc/nginx/sites-enabled/rtk ] || \
  ln -s /etc/nginx/sites-available/rtk /etc/nginx/sites-enabled/rtk
rm -f /etc/nginx/sites-enabled/default

echo "=== 3. Проверка и перезагрузка nginx ==="
nginx -t
systemctl reload nginx
systemctl is-active nginx
echo "=== Готово ==="
