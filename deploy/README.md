# Развёртывание на VPS (приватный AI-сервис)

Пошаговая инструкция для запуска публичного веб-чата с **локальной** LLM на
VPS (Ubuntu 25.10, 2 vCPU / 4 ГБ, без GPU). Полное ТЗ — в
[`docs/vps-llm-service.md`](../docs/vps-llm-service.md).

## Архитектура

```
Интернет ──▶ nginx :8443 (публичный HTTPS, самоподписанный сертификат)
                 │  proxy_pass + X-Forwarded-For
                 ▼
            rtk-web :127.0.0.1:8000  (app-сервис, Python)
                 │  HTTP (OpenAI-совместимый API, Bearer)
                 ▼
            llama-server :127.0.0.1:8080  (движок, .gguf, CPU-only)
```

- Наружу открыты **только 22 (SSH) и 8443 (nginx, HTTPS)**. Порт 80 не
  слушается (меньше шума от сканеров), HTTPS — на нестандартном порту.
- Порт движка (`8080`) и app (`8000`) — только loopback.
- Внешние порты дополнительно ограничены **ufw**, SSH защищён **fail2ban** —
  см. раздел «Безопасность» ниже.

## Что уже реализовано в коде

| Возможность | Где | Пункт ТЗ |
|---|---|---|
| Лимит активных сессий = 8 + заглушка 503 | `web/session_manager.py`, `web/server.py` | §5.1, §5.3 |
| Приоритетный слот владельца (`RTK_OWNER_TOKEN`) | `web/session_manager.py`, `web/server.py` | §5.3 |
| Ротация слота (обновление страницы / «новая сессия») | `web/server.py`, `js/features/sessionGuard.js` | §5.3 |
| Сессия на клиента (cookie `sid`, изоляция) | `web/session_manager.py` | §5.2 |
| Автомат `ACTIVE→CHECK→OFFLINE`, фоновая очистка | `web/session_manager.py` | §5.1, §5.3 |
| `/api/heartbeat`, `/api/health` | `web/server.py` | §5.4, §8.2 |
| Клиентский сторож (диалог «Вы здесь?», read-only) | `js/features/sessionGuard.js` | §5.4, §5.5 |
| Rate limit по IP (token bucket, 429) | `web/rate_limit.py`, `web/server.py` | §7.4 |
| ENV-конфиг, loopback, флаг «без ключей» | `rtk_app/config.py`, `rtk_web.py` | §6.2 |

## Установка (кратко)

```bash
# 1. Подготовка кода и модели
sudo mkdir -p /opt/rtk && cd /opt/rtk
sudo git clone <РЕПОЗИТОРИЙ> .
sudo mkdir -p /opt/models
# положите модель .gguf (напр. Qwen2.5-3B-Instruct Q4_K_M) в /opt/models/

# 2. Движок llama.cpp (prebuilt или из исходников) — бинарник в /usr/local/bin
#    https://github.com/ggml-org/llama.cpp (сборка CPU-only, AVX2)

# 3. Установка
sudo bash deploy/install.sh

# 4. HTTPS (самоподписанный серт + nginx на 8443)
sudo sh deploy/setup-https.sh

# 5. Фаервол и защита SSH
sudo sh deploy/setup-firewall.sh
sudo sh deploy/setup-fail2ban.sh

# 6. Включение сервисов
sudo systemctl enable --now llama-server.service
sudo systemctl enable --now rtk-web.service
sudo systemctl restart nginx
```

## Проверка

```bash
# Сервисы живы?
systemctl status llama-server rtk-web nginx

# Здоровье app + счётчик сессий
curl -s http://127.0.0.1:8000/api/health

# Внешний доступ (с другой машины) — страница чата должна открыться
curl -sk https://<IP>:8443/ | head
```

## Конфигурация (ENV)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `RTK_HOST` | `127.0.0.1` | адрес app (loopback за nginx) |
| `RTK_PORT` | `8000` | порт app |
| `RTK_BEHIND_PROXY` | `0` | доверять `X-Forwarded-For` (за nginx = 1) |
| `RTK_MAX_SESSIONS` | `8` | лимит активных сессий (0 = выключить) |
| `RTK_IDLE_TIMEOUT` | `60` | секунд тишины → `CHECK` |
| `RTK_CHECK_TIMEOUT` | `30` | секунд без ответа → `OFFLINE` |
| `RTK_OWNER_TOKEN` | *(пусто)* | секрет владельца: владелец открывает `/?owner=<токен>` и **всегда** получает слот; при исчерпании лимита вытесняется самая старая обычная сессия, а бывший `offline` владельца автоматически получает новый слот (пусто = приоритет выключен) |
| `RTK_RATE_LIMIT_PER_MIN` | `10` | запросов/мин на IP (0 = выключить) |
| `RTK_SKIP_KEY_CHECK` | `0` | не проверять ключи облачных моделей |
| `RTK_CHECK_NETWORK` | `1` | сетевые проверки при самодиагностике |

## Безопасность

Внешний периметр ограничивается тремя слоями:

1. **loopback-сервисы.** Движок (`8080`) и app (`8000`) слушают только
   `127.0.0.1` — снаружи недоступны в принципе.
2. **ufw** (`deploy/setup-firewall.sh`). Политика: входящие — deny, исходящие —
   allow. Разрешены только `22/tcp` (SSH) и `8443/tcp` (HTTPS). Скрипт
   идемпотентен, правило для SSH добавляется **до** включения ufw.
   Параметры: `RTK_SSH_PORT` (22), `RTK_HTTPS_PORT` (8443).
3. **fail2ban** (`deploy/setup-fail2ban.sh`). Jail `sshd` через journald:
   5 неудач за 10 минут → бан на 1 час. Параметр: `RTK_SSH_PORT` (22).

```bash
# Проверка фаервола и банов
sudo ufw status verbose
sudo fail2ban-client status sshd
```

> ВАЖНО: перед сменой `RTK_SSH_PORT` убедись, что новый порт реально слушается
> sshd, и открой его в ufw — иначе можно потерять доступ к серверу.

## Эксплуатация

```bash
# Обновление кода
cd /opt/rtk && sudo git pull
sudo systemctl restart rtk-web

# Логи
journalctl -u rtk-web -f
journalctl -u llama-server -f

# Память под нагрузкой (должно быть < 4 ГБ)
free -h
```

## Чек-лист приёмки

Полный список — §11 ТЗ. Ключевое:

1. С внешней машины открывается страница чата.
2. Восемь сессий работают независимо; девятая получает заглушку «нет свободной
   сессии». Владелец (`?owner=<токен>`) получает слот всегда — при нехватке
   вытесняется самая старая обычная сессия.
3. После 5 мин тишины — диалог «Вы здесь?»; после 1 мин без ответа сессия
   завершается, слот освобождается.
4. При завершении история видна, ввод заблокирован.
5. `8080` и `8000` недоступны извне; снаружи открыты только 22 и 8443.
