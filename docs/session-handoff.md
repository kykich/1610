# Handoff: AI-ассистент разработчика

**Дата:** 18.10.2026  
**Проект:** локальный веб-ассистент с RAG, MCP, AI-ревью PR,
поддержкой пользователей и работой с файлами.  
**Рабочая копия:** `G:\9potok\1610` (`G:\9potok\1410` оставлена как резервная).

## 1. Статус задач

| Задача | Статус | Где смотреть |
|---|---|---|
| 1. RAG по README/docs/коду + MCP git + `/help` | Готово | `rtk_app/rag.py`, `project_server.py`, `docs/REPORT_task6_traceability.md` |
| 2. AI-ревью PR (diff + RAG, `/review`) | Готово | `rtk_app/pr_review.py`, `review_pr.py`, `docs/REPORT_task7_traceability.md` |
| 3. Мини-сервис поддержки (FAQ, тикеты, MCP) | Готово | `rtk_app/support.py`, `support_server.py`, `docs/REPORT_task8_traceability.md` |
| 4. Ассистент по файлам проекта | Готово | `rtk_app/file_tasks.py`, `files_server.py`, `files_cli.py`, `docs/REPORT_task9_traceability.md` |

Последние коммиты:

```text
338e66a feat(files): файловый ассистент, /files и auto-выбор MCP
8b3a8d9 docs: обновить README, handoff и отчёты по файлам/MCP
87b8487 style(ui): зелёный фон поля ввода запроса
```

## 2. Что умеет ассистент

### Задача 1 — RAG и `/help`

- RAG индексирует `README.md`, `docs/`, исходники и PDF.
- Секреты (`apidpsk.txt`, `gigakey.txt`, `.env` и др.) не индексируются.
- `/help` без модели возвращает детерминированную справку: git-ветку, HEAD,
  структуру проекта, состояние индекса и найденные фрагменты.
- `/help` с моделью отвечает по RAG + git и показывает источники.

### Задача 2 — AI-ревью PR

- `rtk_app/pr_review.py`: `collect_changes`, `build_rag_query`,
  `rag_context`, `build_messages`, `format_review`, `review_changes`.
- `review_pr.py` — CLI для локального запуска и GitHub Actions.
- `/review [base] [head] [--no-rag]` — ревью прямо в чате.
- Для reasoning-моделей thinking отключается, чтобы `max_tokens` не уходили
  в `reasoning_content`.
- Большие diff обрезаются; RAG опционален и не ломает ревью при недоступности
  Ollama/индекса.

### Задача 3 — поддержка пользователей

- `rtk_app/support.py`: пользователи, тикеты, контекст обращения, FAQ-запрос.
- `support_server.py` (MCP `support`): `find_user`, `get_ticket`,
  `list_tickets`, `get_support_context`, `stats`.
- `/support T-1001 …` использует MCP, при недоступности — JSON fallback.
- RAG по FAQ ограничен `docs/support`.

### Задача 4 — работа с файлами

- `rtk_app/file_tasks.py`: sandbox, чтение, поиск, preview/apply, diff.
- `files_server.py` (MCP `files`): `list_files`, `read_file`,
  `read_many_files`, `search_files`, `file_info`, `preview_file_change`,
  `apply_file_change(confirm=True)`.
- `files_cli.py`: сценарии `find`, `docs`, `new`.
- `/files find`, `/files docs`, `/files new`, `/files edit`.
- `/files edit` без пути сам выбирает файл по терминам инструкции.
- Обычный чат также понимает запросы вида:
  «добавь серый фон в поле ввода запроса», «измени MODEL…».
- По умолчанию показывается diff; запись — только по явному `--apply` или
  `confirm=True` (для естественного чата — если не сказано «покажи diff»).

## 2a. Модель эмбеддингов RAG (Ollama) — 19.10.2026

- Новый блок в левой колонке (внутри RAG): **путь → «Сканировать» → выбор
  модели → «Запустить»**. Работает с ПК (`e:/Models`, через ПК-агент) и VPS
  (`/opt/models`).
- Модели читаются из `manifests/blobs` Ollama (embedding-модели первыми),
  поэтому список доступен до запуска сервера.
- **Reuse-first:** если Ollama уже запущена (трей/вручную), используется она;
  второй `ollama serve` не поднимается. Кнопка «Остановить» не трогает
  внешний процесс.
- Настройки движка — `session/embed_model.json` (`embed_url/model/dim/source`).
  RAG по-прежнему включается **вручную**; старт эмбеддингов не включает RAG.
- `rag.embed_text` читает активный движок, поддерживает `/api/embeddings`,
  `/api/embed` и `/v1/embeddings`; в индексе хранится `embed_model/embed_dim`,
  при смене модели UI показывает `embed_mismatch` и просит переиндексацию.
- API: `GET/POST /api/embed` (`scan`, `pc_scan`, `start`, `pc_start`,
  `stop`, `pc_stop`, `status`, `check`, `set`).
- ПК-агент: новые флаги `--embed-root`, `--ollama-models`; эндпоинты
  `/embed/scan|start|stop|status`. Для RAG обязателен `--ollama-port 11434`.
- Тест: `python -m tests.check_embed` (61 OK).

## 2b. Политика запуска моделей (19.10.2026)

- По умолчанию на VPS активна **только слабая модель** `Qwen-2B`
  (`llama-server.service`); `rtk1610-web` при старте больше не переключается
  на сильную модель, даже если туннель ПК уже поднят.
- Фоновый контроль туннеля только **возвращает на слабую** при обрыве;
  автоматического weak → strong нет (`RTK_AUTO_STRONG=0`), переключение —
  действием пользователя.
- Запуск локальной модели и модели эмбеддингов RAG требует предварительного
  сканирования и явного выбора модели; сервер не подставляет «первую».
- Ollama на ПК не автозапускается: ярлык `Ollama.lnk` отключён через
  `StartupApproved` (Диспетчер задач → Автозагрузка). Запуск — вручную или
  кнопкой «Запустить» в блоке «Модель эмбеддингов (RAG)».
- Тест: `python -m tests.check_startup` (15 OK).

## 2c. Ключи облачных моделей — ввод в интерфейсе (21.10.2026)

- В левой колонке появился блок **«Ключи облачных моделей»**: поля DeepSeek
  (`sk-…`) и GigaChat (base64 `client_id:client_secret` или пара `id:secret`),
  кнопки «Проверить» и «Очистить ключи».
- Ключ **живёт только в памяти вкладки** (`js/features/keys.js`): ничего не
  пишется в `localStorage`/`sessionStorage`/cookie. Перезагрузка страницы,
  закрытие окна и «Новый разговор» стирают ключи — их нужно вводить заново.
- На сервер ключи приходят в теле `POST /api/ask` (поле `keys`) и действуют
  только на время запроса: `rtk_app/runtime_keys.py` хранит их в
  `threading.local`, `do_POST` вызывает `runtime_keys.clear()` в `finally`.
- Приоритет: ключ из интерфейса > `apidpsk.txt` / `gigakey.txt` (файлы —
  резервный способ для VPS, CLI и GitHub Actions; ENV-переопределений нет).
- `rtk_app/deepseek.py` и `rtk_app/gigachat.py` читают сессионный ключ
  (`allow_session=True`); для проверки вставленного ключа — `allow_session=False`.
  Кэш OAuth-токенов GigaChat теперь **по учётным данным** (хэш в ключе), а не
  один на процесс.
- API: `GET /api/keys` — наличие файлов и маски (полный ключ не отдаётся);
  `POST /api/keys {action:"status"|"check", provider?, key?}` — проверка
  вставленного ключа по сети (DeepSeek — пробный запрос, GigaChat — OAuth),
  без сохранения.
- Тест: `python -m tests.check_session_keys` (56 OK).

## 3. MCP

Актуальные серверы:

- `project` — git-ветка/статус/diff и список файлов;
- `support` — тикеты и пользователи;
- `files` — чтение/поиск/создание/изменение файлов.

По умолчанию включён режим `auto`: модель получает инструменты всех серверов
и выбирает нужный по запросу. В UI можно закрепить конкретный сервер.
Старые настройки без поля `routing` переводятся в `auto`.

## 4. Проверка

```powershell
python -m tests.check_project_help   # 79 OK
python -m tests.check_embed          # 61 OK
python -m tests.check_startup        # 15 OK
python -m tests.check_pr_review      # 86 OK
python -m tests.check_support        # 106 OK
python -m tests.check_files          # 160 OK
python -m tests.check_mcp_auto       # 51 OK
python -m tests.check_session_keys   # 56 OK
python -m tests.check_server_context # 7 OK
node --check js/app.js
node --check js/features/keys.js
```

Запуск:

```powershell
python rtk_web.py 8123
# http://127.0.0.1:8123/
```

Примеры чата:

```text
/help как устроены RAG и MCP?
/review base=HEAD~1 head=HEAD
/support T-1001 Почему не работает авторизация?
/files найди все места, где используется RAG_MIN_SCORE
/files обнови документацию по изменениям HEAD~1..HEAD
/files создай ADR про выбор SQLite для RAG
/files edit замени MODEL на deepseek-reasoner
```

CLI:

```powershell
python review_pr.py --repo C:\work\project --base HEAD~1 --head HEAD --no-rag
python files_cli.py find RAG_MIN_SCORE --root C:\work\project --ext .py
python files_cli.py docs --root C:\work\project --base HEAD~1 --head HEAD
python files_cli.py new adr "Выбор SQLite для RAG" --root C:\work\project
```

## 5. Ограничения

- Удаление и переименование файлов не реализованы: только create/update.
- Файловые операции ограничены текстовыми файлами и sandbox-корнем.
- Модель возвращает полное новое содержимое файла; очень большие файлы
  ограничены `FILES_MAX_WRITE_CHARS`.
- `docs` требует git-репозиторий в `docs_dir`.
- Для длинных diff используется `--max-tokens` больше 4000.
- GitHub Action требует PAT со scope `workflow` для публикации workflow-файла.

## 6. Следующие шаги

1. При необходимости обновить workflow и выполнить `git push origin master`.
2. Добавить patch-формат изменений и интерактивное подтверждение записи в UI.
3. Добавить запись/обновление тикетов через MCP и реальную CRM.
4. Проверить GitHub Action на реальном PR после обновления токена.

## 7. VPS: развёртывание 1610 (19.10.2026)

- Репозиторий: `https://github.com/kykich/1610`, ветка `main`
  (локальный `master` отслеживает `origin/main`).
- VPS: `/opt/rtk1610`, venv `/opt/rtk1610/.venv` (`mcp`, `pypdf`),
  сервис `rtk1610-web` на `127.0.0.1:8001`.
- Внешний доступ: `https://91.188.213.252:8443/` (nginx → 8001);
  `8444` — прямой дубль 1610. `1210` (`rtk-web`) и `llama-server.service`
  остановлены и `disable`, чтобы не поднимать модель автоматически
  (nginx-бэкап `/etc/nginx/sites-available/rtk.1210.bak`).
- **Слабая модель VPS (`Qwen-2B`) работает всегда:**
  `llama-server.service` включён, `127.0.0.1:8080`.
- **Единый блок «Локальная модель»:** путь → «Сканировать» → выбор
  модели и настроек → «Запустить» / «Остановить». Для пути ПК
  (`e:/Models/llama`) скан и запуск идут через ПК-агент; для
  `/opt/models` — локально на VPS. Отдельные кнопки ПК удалены.
- ENV: `/etc/rtk1610/rtk-web.env` (600), состояние —
  `/var/lib/rtk1610/session`, RAG — общий `rag_index.db` на все сессии
  (`RTK_SHARED_RAG=1`).
- Слабая модель VPS: `Qwen-2B` (`llama-server 127.0.0.1:8080`,
  `qwen2.5-3b-instruct-q4_k_m.gguf`).
- Сильная модель ПК: `Qwen-9B` через reverse-SSH (`18080`), ПК-агент из
  `rtk_pc_agent` (`18081`).
- **RAG включается вручную.** Движок эмбеддингов выбирается в подблоке
  «Модель эмбеддингов (RAG)»: `e:/Models` → «Сканировать» →
  `nomic-embed-text-v2-moe:latest` → «Запустить». Если Ollama уже запущена
  (трей/вручную), используется ОНА — второй процесс не поднимается.
- Для RAG ПК-агент должен пробрасывать Ollama:
  `--ollama-port 11434 --embed-tunnel-port 11435 --embed-root "e:/Models"`.
  При `--ollama-port 0` порта `11435` на VPS нет.
- Индекс RAG (89 файлов, 2486 чанков, nomic 768) совместим с этой моделью —
  переиндексация не нужна, пока модель/размерность не менялись.
- Проверено: ручное переключение `Qwen-9B ↔ Qwen-2B`, авто-возврат при
  обрыве туннеля и авто-возврат на сильную при восстановлении; чат на
  обеих моделях; запуск/остановка модели ПК из единого блока UI.

Команды эксплуатации — `docs/VPS_1610.md`.

