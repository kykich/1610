#!/usr/bin/env bash
# Установка приватного AI-сервиса (публичный чат к локальной LLM) на VPS.
# См. docs/vps-llm-service.md §10.
#
# Использование (от root или через sudo):
#   sudo bash deploy/install.sh
#
# Предполагается, что:
#   * репозиторий склонирован в /opt/rtk (см. ниже — можно изменить APP_DIR);
#   * модель .gguf лежит в /opt/models/;
#   * бинарник llama-server доступен в /usr/local/bin/ (см. §4.2).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rtk}"
MODELS_DIR="${MODELS_DIR:-/opt/models}"
SERVICE_USER="${SERVICE_USER:-rtk}"
PUBLIC_PORT="${PUBLIC_PORT:-80}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "1/8. Проверка окружения"
if [[ $EUID -ne 0 ]]; then
    echo "Запустите от root (sudo)." >&2
    exit 1
fi
command -v nginx >/dev/null || { echo "nginx не установлен: apt-get install -y nginx"; exit 1; }
command -v python3 >/dev/null || { echo "python3 не установлен"; exit 1; }

say "2/8. Swap 2 ГБ (страховка от OOM, §10.3)"
if ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "swap включён."
else
    echo "swap уже настроен — пропускаю."
fi

say "3/8. Пользователь-сервис ${SERVICE_USER} (без root)"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

say "4/8. Каталоги"
mkdir -p "${MODELS_DIR}" "${APP_DIR}/session" /etc/rtk /var/lib/rtk
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}" "${MODELS_DIR}" /var/lib/rtk

say "5/8. Файлы окружения (ключи вне git, права 600)"
if [[ ! -f /etc/rtk/llama.env ]]; then
    cat >/etc/rtk/llama.env <<EOF
# Путь к модели .gguf (см. §4.1). Замените при необходимости.
MODEL=${MODELS_DIR}/model-3b-q4_k_m.gguf
# Имя модели в API.
LLAMA_ALIAS=local-3b
# Контекст и потоки (см. §4.3).
LLAMA_CTX=4096
LLAMA_THREADS=2
# Ключ движка (Bearer). Сгенерируйте случайный: openssl rand -hex 24
LLAMA_KEY=CHANGE_ME_$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')
EOF
    chmod 600 /etc/rtk/llama.env
fi
if [[ ! -f /etc/rtk/rtk-web.env ]]; then
    cat >/etc/rtk/rtk-web.env <<'EOF'
# Дополнительные ENV для app-сервиса (можно оставить пустым).
# Основные параметры заданы в systemd-юните rtk-web.service.
EOF
    chmod 600 /etc/rtk/rtk-web.env
fi

say "6/8. systemd-юниты"
cp "${APP_DIR}/deploy/llama-server.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/rtk-web.service" /etc/systemd/system/
systemctl daemon-reload

say "7/8. nginx (публичный порт ${PUBLIC_PORT})"
sed "s/listen 80 /listen ${PUBLIC_PORT} /; s/\[::\]:80 /[::]:${PUBLIC_PORT} /" \
    "${APP_DIR}/deploy/nginx.conf" >/etc/nginx/sites-available/rtk
ln -sf /etc/nginx/sites-available/rtk /etc/nginx/sites-enabled/rtk
rm -f /etc/nginx/sites-enabled/default
nginx -t

say "8/8. Firewall (ufw): открыть только SSH и публичный порт (§9)"
if command -v ufw >/dev/null; then
    ufw allow 22/tcp || true
    ufw allow "${PUBLIC_PORT}"/tcp || true
    ufw --force enable || true
else
    echo "ufw не установлен — настройте фаервол вручную (открыть 22 и ${PUBLIC_PORT})."
fi

say "Готово. Включение сервисов:"
cat <<EOF
  sudo systemctl enable --now llama-server.service
  sudo systemctl enable --now rtk-web.service
  sudo systemctl restart nginx

Проверка:
  curl -s http://127.0.0.1:8000/api/health
  curl -s http://<IP>:${PUBLIC_PORT}/api/health

Не забудьте положить модель в ${MODELS_DIR} и указать её путь в /etc/rtk/llama.env.
EOF
