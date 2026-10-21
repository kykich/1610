

"""Встроенный HTTP-сервер (стандартная библиотека).

Чат "только в окне": сервер ничего не хранит на диске. История ведётся в
памяти вкладки и присылается целиком в POST /api/ask.

Сервер работает через АГЕНТА (rtk_app.agent.Agent) — отдельную сущность,
которая инкапсулирует всю логику запросов к LLM. Агент принимает вопрос и
историю диалога, сам обращается к моделям через API и возвращает готовый
результат (html, text, ответы моделей). HTTP-обработчик лишь передаёт данные
между браузером и агентом.

Запрос пользователя уходит агенту, который опрашивает модели:
  - DeepSeek-flash,
  - GigaChat (базовая, простая).

JSON-API:
    GET  /                  - страница (index.html)
    GET  /css/*,/js/*       - стили и скрипты
    GET  /docs/<file>       - документ из папки RAG (PDF, README.md,
                              исходник) для ссылок «Источники» в карточке
    GET  /api/model         - список моделей, которые обслуживает агент
    GET  /api/session       - сохранённая история + стратегия + facts + ветки
    POST /api/ask           - {question, models[], max_tokens?, compact?,
                               strategy?} -> ответ.
                               models[] — выбранные модели; у каждой можно
                               задать СВОИ параметры генерации:
                               {label, temperature?, top_p?,
                                repetition_penalty?, max_tokens?,
                                context_window?}. Поля опциональны; null/
                               отсутствие — параметр не применяется.
                               Общий max_tokens запроса — фолбэк для моделей
                               без собственного max_tokens.
                               Команды в question: /help (RAG по проекту),
                               /review (AI-ревью PR), /support (поддержка
                               по тикету/пользователю + FAQ), /files
                               (поиск/обновление/создание файлов проекта).
    POST /api/newchat       - начать новый разговор (очистить историю)
    POST /api/compact       - {enabled, keep} -> настройки сжатия (summary)
    POST /api/compact_summary - {keep} -> дописать вытесненное в summary
    POST /api/strategy      - {strategy, window} -> стратегия контекста
                              (none / sliding / facts / branch)
    POST /api/facts         - {facts:{ключ:значение}} -> сохранить блок facts
    POST /api/memory        - {type:"working"|"longterm", …} -> память агента
                              (действия: set/delete/replace/clear, GET-снимок)
    POST /api/branches      - {action:"create"|"switch"|"delete"|"rename", …}
                              -> ветки (rename: {index, name})
    POST /api/task          - состояние задачи (Task State Machine):
                              {action:"start"|"advance"|"pause"|"resume"|
                               "finish"|"reset"|"state", …} -> этап/шаг/
                              ожидаемое действие (planning->execution->
                              validation->done), пауза/продолжение.
    GET  /api/invariants    - инварианты (правила-ограничения) + категории
    POST /api/invariants    - {action:"add"|"update"|"delete"|"replace"|
                               "clear"|"state", …} -> инварианты (отдельно от
                              диалога; жёсткие правила, нарушать нельзя)
    GET  /api/profiles      - список профилей (персон) + активный
    POST /api/profiles      - {action:"create"|"switch"|"update"|"delete", …}
                              -> профили (персоны): при создании задаются
                              name, model (модель персоны), character
                              (характер/тон) и style (характер ответов).
                              Ответы даёт АКТИВНАЯ персона своей моделью;
                              при отсутствии персон диалог идёт с моделями.
    GET  /api/mcp           - состояние MCP: {enabled, model, tools, status}
                              (настройки + последний статус сервера)
    POST /api/mcp           - {action, …} -> настройки и статус MCP:
                              "state"  — вернуть текущее состояние (по умолч.);
                              "set"    — сохранить {enabled?, model?, server?};
                              "status" — ПРОВЕРИТЬ статус MCP-сервера
                                         (подключиться, получить список
                                         инструментов и вернуть результат);
                              "compose"— ЗАПУСТИТЬ КОМПОЗИЦИЮ MCP-серверов
                                         (пайплайн календарь → замполит →
                                         завхоз; см. docs/task5.md) и вернуть
                                         отчёт о прогоне. Композиция
                                         ВЗАИМОИСКЛЮЧАЮЩА с чекбоксом
                                         «Использовать MCP»: её запуск
                                         выключает чекбокс, а включение
                                         чекбокса — останавливает композицию
                                         (см. ниже);
                              "compose_stop" — остановить режим композиции
                                         (возврат к отдельным серверам).
    GET  /api/rag           - состояние RAG: {enabled, docs_dir, top_k,
                              embed_model, files, chunks, available, errors}
    POST /api/rag           - {action, …} -> RAG (Retrieval-Augmented
                              Generation) поверх PDF, документации и кода:
                              "state"   — вернуть состояние (по умолч.);
                              "set"     — сохранить {enabled?, docs_dir?,
                                          top_k?} (настраиваемый путь к
                                          папке с документами + число
                                          фрагментов);
                              "reindex" — (пере)индексировать папку:
                                          PDF (текстовый слой), README/docs
                                          и исходники -> чанки ->
                                          эмбеддинги (Ollama
                                          nomic-embed-text-v2-moe) -> SQLite;
                              "clear"   — очистить индекс;
                              "check"   — проверить модель эмбеддингов.
                              При ВКЛЮЧЁННОМ RAG найденный контекст
                              подмешивается в промпт (режим A2).
    GET  /api/local         - состояние ЛОКАЛЬНОЙ модели: {enabled, root,
                              server_path, params, models, servers,
                              running, runtime}
    POST /api/local         - {action, …} -> ЛОКАЛЬНАЯ модель (llama.cpp /
                              Ollama / LM Studio) на этом компьютере:
                              "state"  — вернуть состояние (по умолч.);
                              "scan"   — просканировать КОРЕНЬ {root}: найти
                                         сервер (llama-server.exe) и *.gguf,
                                         предложить параметры запуска;
                              "params" — подобрать параметры для {model}
                                         (ngl — автоподбор по видеопамяти);
                              "start"  — запустить сервер {server_path?,
                                         params?}, дождаться готовности,
                                         вернуть URL (…/v1); модель добавляется
                                         в список источников агента;
                              "stop"   — остановить сервер (вручную);
                              "status" — проверить живость и список моделей;
                              "models" — список моделей запущенного сервера;
                              "set"    — сохранить {enabled?, root?,
                                         server_path?, params?}.
    GET  /api/embed         - состояние МОДЕЛИ ЭМБЕДДИНГОВ RAG (Ollama):
                              {settings, runtime, models, servers, source,
                               reuse_offer, active, tunnel}
    POST /api/embed         - {action, …} -> Ollama для RAG (reuse-first):
                              "scan"/"pc_scan" — корень {root};
                              "start"/"pc_start" — {root?, model?, reuse?}:
                                         если Ollama уже запущена, используется
                                         она (второй процесс не поднимается);
                              "stop"/"pc_stop" — только свой процесс;
                              "status"/"check" — /api/tags, /api/ps, dim;
                              "set" — сохранить {root?, exe?, models_dir?,
                                         model?, source?, reuse?}.
    GET  /api/keys          - ключи облачных моделей, введённые в браузере:
                              {deepseek:{file,masked}, gigachat:{file,masked}}.
                              Полные значения НИКОГДА не возвращаются.
    POST /api/keys          - {action:"status"|"check", provider?, key?}:
                              "status" — как GET;
                              "check"  — проверить вставленный ключ по сети
                                         (DeepSeek — пробный запрос, GigaChat —
                                         OAuth-токен), ничего не сохраняя.
                              Ключи из интерфейса приходят в /api/ask полем
                              "keys" и действуют только на время запроса.
    """
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
import webbrowser
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rtk_app import config
from rtk_app import runtime_keys
from rtk_app.agent import Agent
from rtk_app import file_tasks
from rtk_app import local_server
from rtk_app import ollama_server
from rtk_app import support as support_mod
from rtk_app.session_store import SessionStore
from web import session_manager as session_mgr
from web import rate_limit

try:
    # MCP-клиент (опциональная зависимость): сервер проекта должен
    # запускаться даже без пакета mcp — тогда статус MCP будет «недоступен».
    from rtk_app import mcp_client
except Exception:  # pragma: no cover — зависит от окружения
    mcp_client = None


# ----------------------------------------------------------------------
# Команда /help (docs/Final_task.md, задача 1): ассистент разработчика
# ----------------------------------------------------------------------
# /help <вопрос> — ответ о проекте с опорой на документацию и код (RAG)
# и на сведения из git (MCP-сервер «Проект»). Команда работает и без
# выбранной модели: тогда возвращается детерминированная справка.
_HELP_RE = re.compile(r"^\s*/help\b[:\s]*(.*)$", re.IGNORECASE | re.DOTALL)
_HELP_DEFAULT_QUESTION = (
    "Расскажи о структуре проекта: назначение, основные модули и где "
    "лежит документация.")
# Сколько фрагментов показывать в справке без модели.
_HELP_HITS_PREVIEW = 5
# Сколько символов текста фрагмента выводить в справке.
_HELP_HIT_CHARS = 160
# Заглушка, если в целевой папке нет git-данных (не репозиторий).
NO_GIT_TEXT = "Нет данных о GIT"


def parse_help(question):
    """Возвращает текст запроса после /help или None (не команда /help)."""
    match = _HELP_RE.match(str(question or ""))
    if not match:
        return None
    return (match.group(1) or "").strip()


# ----------------------------------------------------------------------
# Команда /support (docs/Final_task.md, задача 3): поддержка пользователей
# ----------------------------------------------------------------------
# /support T-1001 Почему не работает авторизация?
# /support ticket=T-1001 ...
# /support user=anna@example.com ...
# --no-rag — не подмешивать FAQ.
_SUPPORT_RE = re.compile(r"^\s*/support\b(.*)$", re.IGNORECASE | re.DOTALL)


def parse_support(question):
    """Разбирает /support T-1001 [вопрос] и возвращает dict или None.

    Возвращает {ticket_id, user_id, query, use_rag}. Тикет можно указать
    первым токеном (T-1001, #T-1001, 1001) либо ticket=T-1001; пользователя —
    user=email/id. Остальные токены — текст вопроса.
    """
    match = _SUPPORT_RE.match(str(question or ""))
    if not match:
        return None
    ticket_id, user_id, use_rag = "", "", True
    query_parts = []
    for index, token in enumerate((match.group(1) or "").split()):
        low = token.lower()
        if low in ("--no-rag", "no-rag"):
            use_rag = False
        elif low.startswith("ticket="):
            ticket_id = token.split("=", 1)[1].strip()
        elif low.startswith("user="):
            user_id = token.split("=", 1)[1].strip()
        elif (not ticket_id
              and re.fullmatch(r"#?T-\d+", token, re.IGNORECASE)):
            ticket_id = token
        elif (not ticket_id and index == 0
              and re.fullmatch(r"#?\d{3,}", token)):
            # Числом тикет задаётся ТОЛЬКО первым токеном: иначе код ошибки
            # («401») в вопросе был бы принят за ID обращения.
            ticket_id = token
        else:
            query_parts.append(token)
    return {"ticket_id": ticket_id, "user_id": user_id,
            "query": " ".join(query_parts).strip(), "use_rag": use_rag}


# ----------------------------------------------------------------------
# Команда /files (docs/Final_task.md, задача 4): работа с файлами проекта
# ----------------------------------------------------------------------
_FILES_RE = re.compile(r"^\s*/files\b(.*)$", re.IGNORECASE | re.DOTALL)


def _looks_like_path_token(token):
    """Похож ли токен на имя файла/путь (для /files edit)."""
    value = str(token or "").strip().strip("\"'")
    if not value:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    if "/" in value or "\\" in value:
        return True
    if re.search(r"\.[A-Za-z0-9]{1,8}$", value):
        return True
    return "_" in value


def _is_chat_edit_request(question):
    """Похож ли обычный запрос чата на задачу изменения файла."""
    low = str(question or "").lower()
    actions = ("добавь", "добавить", "измени", "изменить", "замени",
               "заменить", "исправь", "поправь", "поставь", "сделай",
               "убери", "примени")
    targets = ("файл", "css", "html", "фон", "цвет", "стил", "шрифт",
               "отступ", "рамк", "кнопк", "поле ввода", "input", "textarea",
               "readme", "документац", "код", "model", "модель", "config")
    return (any(x in low for x in actions)
            and any(x in low for x in targets))


def _chat_edit_apply(question):
    """Писать изменение сразу или только показать diff."""
    low = str(question or "").lower()
    return not any(x in low for x in ("покажи", "не записывай", "не меняй",
                                      "preview", "diff", "сначала"))


def _is_chat_create_request(question):
    """Просят создать новый файл, а не изменить существующий."""
    low = str(question or "").lower()
    actions = ("создай", "создать", "новый файл", "new file", "сделай файл")
    return any(x in low for x in actions) and ("файл" in low or ".md" in low
                                               or ".txt" in low)


def _chat_create_path(question):
    """Имя файла из фразы «создай info.md в корне»."""
    for token in str(question or "").split():
        value = token.strip(" .,:;()[]{}\"'")
        if _looks_like_path_token(value):
            return value
    return ""


def parse_files(question):
    """Разбирает /files <цель> и возвращает dict или None.

    Явные формы:
      /files find <запрос>
      /files docs [base] [head] [--apply] [--no-rag]
      /files new <adr|changelog|readme|doc> <тема> [--apply]
      /files edit [путь] <инструкция> [--apply]

    Естественная цель тоже поддерживается: «найди использования…»,
    «обнови документацию…», «создай ADR…». Пользователь задаёт цель, а файлы
    выбирает сам ассистент.
    """
    match = _FILES_RE.match(str(question or ""))
    if not match:
        return None
    raw = (match.group(1) or "").strip()
    tokens = raw.split()
    first = tokens[0].lower() if tokens else ""
    explicit_docs = first in ("docs", "doc", "documentation")
    scenario = ""
    apply_change = False
    use_rag = True
    base = head = kind = topic = path = instruction = ""
    rest = []
    for token in tokens:
        low = token.lower()
        if low in ("--apply", "apply"):
            apply_change = True
        elif low in ("--no-rag", "no-rag"):
            use_rag = False
        elif low.startswith("base="):
            base = token.split("=", 1)[1].strip()
        elif low.startswith("head="):
            head = token.split("=", 1)[1].strip()
        elif low.startswith("kind="):
            kind = token.split("=", 1)[1].strip()
        elif low.startswith("topic="):
            topic = token.split("=", 1)[1].strip()
        elif low.startswith("path="):
            path = token.split("=", 1)[1].strip()
        elif not scenario and low in ("find", "search", "usages", "найди",
                                      "поиск"):
            scenario = "find"
        elif not scenario and low in ("docs", "doc", "documentation",
                                      "обнови", "документация"):
            scenario = "docs"
        elif not scenario and low in ("new", "create", "generate", "создай",
                                      "сгенерируй", "новый"):
            scenario = "new"
        elif not scenario and low in ("edit", "отредактируй", "правка",
                                      "правь"):
            scenario = "edit"
        else:
            rest.append(token)
    if not scenario:
        low = raw.lower()
        if not low:
            scenario = "help"
        elif any(x in low for x in ("найд", "где использ", "вызыв", "упомин",
                                     "search", "find")):
            scenario = "find"
        elif (any(x in low for x in ("измени", "изменить", "отредактируй",
                                     "правь", "edit"))
              and any(_looks_like_path_token(t) for t in tokens)):
            scenario = "edit"
        elif any(x in low for x in ("обнови", "документац", "docs", "readme")):
            scenario = "docs"
        elif any(x in low for x in ("создай", "сгенерируй", "новый файл",
                                     "adr", "changelog")):
            scenario = "new"
        elif any(x in low for x in ("измени", "изменить", "отредактируй",
                                     "правь", "edit")):
            scenario = "edit"
        else:
            scenario = "find"
    if scenario == "edit":
        words = list(rest)
        if not path:
            path_index = None
            for i, token in enumerate(words):
                if (token.lower() in ("файле", "файл", "файла", "для")
                        and i + 1 < len(words)):
                    path_index = i + 1
                    break
            if path_index is None:
                for i, token in enumerate(words):
                    if _looks_like_path_token(token):
                        path_index = i
                        break
            if path_index is not None:
                path = words[path_index].strip("\"'")
                words = words[:path_index] + words[path_index + 1:]
        while words and words[0].lower() in ("в", "файле", "файл",
                                             "файла", "для"):
            words.pop(0)
        instruction = " ".join(words).strip()
        return {"scenario": "edit", "query": "", "base": "", "head": "",
                "kind": "", "topic": "", "path": path,
                "instruction": instruction, "apply": apply_change,
                "use_rag": use_rag}
    if scenario == "find":
        query = " ".join(rest).strip() or raw
        return {"scenario": "find", "query": query, "base": "", "head": "",
                "kind": "", "topic": "", "path": "", "apply": apply_change,
                "use_rag": use_rag}
    if scenario == "docs":
        revs = []
        if explicit_docs:
            revs = [token for token in rest
                    if re.match(r"^[A-Za-z0-9][A-Za-z0-9._~^/-]*$", token)]
        if not base:
            base = revs[0] if revs else "HEAD~1"
        if not head:
            head = revs[1] if len(revs) > 1 else "HEAD"
        return {"scenario": "docs", "query": "", "base": base, "head": head,
                "kind": "", "topic": "", "path": "", "apply": apply_change,
                "use_rag": use_rag}
    if scenario == "new":
        known = ("adr", "changelog", "readme", "doc", "file")
        if not path and rest and _looks_like_path_token(rest[0]):
            path = rest.pop(0).strip("\"'")
        if not kind and rest and rest[0].lower() in known:
            kind = rest.pop(0).lower()
        elif not kind:
            kind = "adr"
        if not topic:
            topic = " ".join(rest).strip() or "последние изменения проекта"
        return {"scenario": "new", "query": "", "base": "", "head": "",
                "kind": kind, "topic": topic, "path": path,
                "apply": apply_change, "use_rag": use_rag}
    return {"scenario": "help", "query": "", "base": "", "head": "",
            "kind": "", "topic": "", "path": "", "apply": False,
            "use_rag": use_rag}


def support_lookup(ticket_id="", user_id="", query="", use_mcp=True,
                   timeout=None):
    """Данные тикета/пользователя для /support.

    Сначала вызывается MCP-инструмент ``get_support_context`` сервера
    «Поддержка» (JSON-тикеты/пользователи как CRM). Если MCP недоступен или
    вернул ошибку — читаем те же JSON напрямую (fallback), чтобы команда
    работала даже без установленного пакета ``mcp``.

    Возвращает dict {ok, ticket, user, text, rag_query, source, error}, где
    source — "mcp" | "json" | "none" (общий вопрос о продукте без обращения).
    """
    tid = str(ticket_id or "").strip()
    uid = str(user_id or "").strip()
    text_query = str(query or "").strip()
    if not tid and not uid:
        # Общий вопрос о продукте: данных обращения нет, FAQ ищется по тексту.
        return {"ok": True, "ticket": None, "user": None, "text": "",
                "rag_query": support_mod.build_rag_query(text_query),
                "source": "none", "error": None}
    if use_mcp and mcp_client is not None:
        try:
            result = mcp_client.mcp_call_tool(
                "get_support_context",
                {"ticket_id": tid, "user_id": uid, "query": text_query},
                timeout=timeout,
                server_id=getattr(config, "SUPPORT_SERVER_ID", "support"))
            if result.get("ok"):
                data = json.loads(result.get("text") or "{}")
                if isinstance(data, dict) and data.get("ok"):
                    data["source"] = "mcp"
                    return data
                print("[SUPPORT] MCP вернул ошибку: %s"
                      % ((data or {}).get("error")
                         if isinstance(data, dict) else data), flush=True)
            else:
                print("[SUPPORT] MCP недоступен: %s"
                      % result.get("error"), flush=True)
        except Exception as exc:                 # noqa: BLE001
            print("[SUPPORT] MCP-вызов не выполнен: %s" % exc, flush=True)
    try:
        context = support_mod.resolve_context(
            ticket_id=tid, user_id=uid, query=text_query, allow_empty=True)
        context["source"] = "json"
        return context
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "ticket": None, "user": None, "text": "",
                "rag_query": support_mod.build_rag_query(text_query),
                "source": "json", "error": str(exc)}


def support_rag_context(rag, query, top_k=None, docs_dir=None):
    """RAG-контекст по FAQ/документации поддержки.

    Поиск ограничен папкой ``SUPPORT_DOCS_DIR`` (docs/support), поэтому FAQ
    не смешивается с кодом и документацией других проектов в общем индексе.
    Возвращает (context_text, hits); ошибки поиска не ломают ответ.
    """
    target = os.path.abspath(str(docs_dir or support_mod.docs_dir()))
    try:
        if not rag.status().get("available"):
            return "", []
        return rag.build_context(
            query, top_k=(top_k or getattr(config, "SUPPORT_TOP_K", 5)),
            docs_dir=target)
    except Exception as exc:                     # noqa: BLE001
        print("[SUPPORT] RAG-поиск по FAQ не выполнен: %s" % exc, flush=True)
        return "", []


# ----------------------------------------------------------------------
# Команда /review (docs/Final_task.md, задача 2): AI-ревью PR прямо в чате
# ----------------------------------------------------------------------
# /review [base] [head] [--no-rag]
#   по умолчанию base=HEAD~1, head=HEAD — ревью последнего коммита;
#   можно указать ревизии (например: /review origin/master HEAD);
#   --no-rag — не подмешивать контекст RAG (быстрее, дешевле).
_REVIEW_RE = re.compile(r"^\s*/review\b(.*)$", re.IGNORECASE | re.DOTALL)
_REVIEW_DEFAULT_BASE = "HEAD~1"
_REVIEW_DEFAULT_HEAD = "HEAD"


def parse_review(question):
    """Разбирает /review [base] [head] [--no-rag].

    Возвращает dict {base, head, use_rag} или None, если это не /review.
    """
    match = _REVIEW_RE.match(str(question or ""))
    if not match:
        return None
    use_rag = True
    parts = []
    for token in (match.group(1) or "").split():
        if token.lower() in ("--no-rag", "no-rag"):
            use_rag = False
        else:
            parts.append(token)
    return {"base": parts[0] if parts else _REVIEW_DEFAULT_BASE,
            "head": parts[1] if len(parts) > 1 else _REVIEW_DEFAULT_HEAD,
            "use_rag": use_rag}


def project_git_note(docs_dir=None, timeout=15):
    """Сведения о git-ветке через MCP-сервер «Проект».

    docs_dir — папка проиндексированных документов: если она (или её предок)
    является git-репозиторием, берём ветку ИМЕННО этого репозитория.
    Если git-данных в папке нет — возвращается заглушка «Нет данных о GIT»,
    БЕЗ подмены веткой проекта ассистента.
    """
    if mcp_client is None:
        return NO_GIT_TEXT

    def _call(args):
        try:
            res = mcp_client.mcp_call_tool("git_branch", args, timeout=timeout,
                                           server_id="project")
        except Exception as exc:                 # noqa: BLE001
            print("[HELP] git-инструмент недоступен: %s" % exc, flush=True)
            return ""
        if not res.get("ok"):
            return ""
        return str(res.get("text") or "").strip()

    root = str(docs_dir or "").strip()
    if not root:
        return _call({})
    text = _call({"repo": root})
    if not text or text.startswith(NO_GIT_TEXT) or text.startswith("Ошибка"):
        return NO_GIT_TEXT
    return text


def project_brief(root=None):
    """Краткая структура проекта (верхний уровень + docs) для /help.

    root — папка проиндексированных документов; если она существует,
    структура берётся из неё (актуально, когда проиндексирован ДРУГОЙ
    проект). По умолчанию — корень проекта ассистента.
    """
    base = getattr(config, "BASE_DIR", os.getcwd())
    root = str(root or "").strip() or base
    if not os.path.isdir(root):
        root = base
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return ""
    # Секретные файлы (ключи API) и служебные папки в справке не показываем.
    skip_files = set(str(f).lower()
                     for f in getattr(config, "RAG_DOC_SKIP_FILES", ()))
    skip_dirs = {"__pycache__", "session", "node_modules", "rag_images"}
    dirs = [n for n in names if os.path.isdir(os.path.join(root, n))
            and not n.startswith(".") and n.lower() not in skip_dirs]
    files = [n for n in names if os.path.isfile(os.path.join(root, n))
             and not n.startswith(".") and n.lower() not in skip_files]
    parts = []
    if dirs:
        parts.append("папки: " + ", ".join(dirs) + "/")
    if files:
        parts.append("файлы: " + ", ".join(files))
    docs = os.path.join(root, "docs")
    if os.path.isdir(docs):
        try:
            md = sorted(n for n in os.listdir(docs) if n.lower().endswith(
                (".md", ".txt", ".rst")))
        except OSError:
            md = []
        if md:
            parts.append("документация docs/: " + ", ".join(md))
    return "\n".join(parts)


class _ServerState:
    """Глобальное состояние сервера: агент (единая сущность) и сессия."""
    agent = None
    session = None
    # Настройки MCP (включается чекбоксом в левой колонке):
    #   enabled — использовать ли инструменты MCP;
        #   model   — метка модели, применяемой при работе с MCP;
    #   status  — последний результат проверки статуса MCP-сервера.
    mcp_enabled = getattr(config, "MCP_ENABLED", False)
    mcp_model = getattr(config, "MCP_MODEL", "")
    # id выбранного MCP-сервера (см. config.MCP_SERVERS) либо "auto" —
    # модель сама выбирает сервер и инструмент по запросу.
    mcp_server = getattr(config, "MCP_SERVER_DEFAULT", "auto")
    mcp_status = None
    mcp_lock = threading.Lock()
    # Композиция MCP удалена. Поля оставлены только для совместимости со
    # старыми настройками/клиентами и всегда равны False/None.
    composition_running = False
    composition_thread = None
    composition_stop = None

    # --- ЛОКАЛЬНАЯ МОДЕЛЬ (llama.cpp / Ollama) ---
    # Менеджер процесса локального сервера (один процесс на сервер).
    local_manager = None
    # Кеш последнего сканирования корня (серверы, модели).
    local_scan = None
    # Последний статус запущенного сервера (runtime).
    local_runtime = None
    local_lock = threading.Lock()

    # --- МОДЕЛЬ ЭМБЕДДИНГОВ RAG (Ollama) ---
    # Менеджер Ollama (reuse-first: уже запущенную не дублирует).
    embed_manager = None
    # Кеш сканирования корня на VPS и на ПК.
    embed_scan = None
    pc_embed_scan = None
    embed_lock = threading.Lock()

    # --- ЛОКАЛЬНАЯ LLM С ПК (SSH-ТУННЕЛЬ) ---
    # Активный движок: "weak" (Qwen-2B на VPS, 127.0.0.1:8080) либо
    # "strong" (Qwen-9B на ПК, виден как 127.0.0.1:TUNNEL_PORT).
    local_engine = "weak"
    # Последний результат сканирования папки НА ПК (служебный API агента).
    pc_scan = None
    # Фоновый контроль туннеля (двусторонний авто-возврат weak <-> strong).
    tunnel_thread = None
    tunnel_stop = None
    tunnel_lock = threading.Lock()
    # VPS: реестр клиентских сессий (лимит активных + автомат состояний).
    _RTK_VPS_SESSIONS = session_mgr.get_manager()


def _mcp_settings_file():
    """Путь к файлу настроек MCP (в папке сессии)."""
    return getattr(config, "MCP_SETTINGS_FILE", None)


def _mcp_auto_id():
    """id режима автовыбора MCP-сервера моделью."""
    return str(getattr(config, "MCP_AUTO_ID", "auto"))


def _mcp_valid_server_ids():
    """Допустимые id: все реальные серверы + режим авто."""
    ids = {str(s.get("id")) for s in
           (getattr(config, "MCP_SERVERS", []) or [])}
    ids.add(_mcp_auto_id())
    return ids


def _mcp_is_auto(server_id=None):
    """True, если выбран режим автовыбора сервера."""
    return str(server_id or _ServerState.mcp_server or "") == _mcp_auto_id()


def _load_mcp_settings():
    """Загружает настройки MCP из файла (если есть), заполняя состояние."""
    path = _mcp_settings_file()
    if not path or not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _ServerState.mcp_enabled = bool(data.get("enabled",
                                                   _ServerState.mcp_enabled))
            model = data.get("model")
            if isinstance(model, str):
                _ServerState.mcp_model = model
            server = data.get("server")
            # Новый формат хранит routing="server", если пользователь явно
            # закрепил сервер. Старые настройки (без routing) переводим в
            # авто-режим: модель сама выберет сервер по запросу.
            routing = str(data.get("routing") or "").strip().lower()
            if (routing == "server" and isinstance(server, str)
                    and server in _mcp_valid_server_ids()):
                _ServerState.mcp_server = server
            else:
                # В настройках мог остаться удалённый сервер (calendar/doska/…).
                _ServerState.mcp_server = getattr(
                    config, "MCP_SERVER_DEFAULT", _mcp_auto_id())
            # Композиция MCP удалена; старые настройки игнорируем.
            _ServerState.composition_running = False
    except Exception as exc:
        print("[MCP] не удалось загрузить настройки: %s" % exc, flush=True)


def _save_mcp_settings():
    """Сохраняет настройки MCP на диск (в папку сессии)."""
    path = _mcp_settings_file()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"enabled": _ServerState.mcp_enabled,
                       "model": _ServerState.mcp_model,
                       "server": _ServerState.mcp_server,
                       "routing": ("server"
                                   if _ServerState.mcp_server
                                   and not _mcp_is_auto()
                                   else "auto")}, f,
                      ensure_ascii=False, indent=2)
    except Exception as exc:
        print("[MCP] не удалось сохранить настройки: %s" % exc, flush=True)


# ---------------------------------------------------------------------------
# ЛОКАЛЬНАЯ МОДЕЛЬ: менеджер процесса + синхронизация с агентом
# ---------------------------------------------------------------------------
def _local_manager():
    """Лениво создаёт и возвращает менеджер локального сервера (singleton)."""
    if _ServerState.local_manager is None:
        _ServerState.local_manager = local_server.LocalServerManager()
    return _ServerState.local_manager


def _local_status_payload():
    """Состояние блока локальной модели для UI.

    Включает: настройки (enabled/root/server_path/params), найденные модели
    и серверы (из последнего сканирования), состояние движка
    (running/ready/url/pid/models), состояние туннеля к ПК и активный движок.
    """
    mgr = _local_manager()
    settings = mgr.load_settings()
    # Всегда опрашиваем /v1/models: на VPS llama-server поднят systemd,
    # а не нашим подпроцессом, поэтому is_running() недостаточно.
    runtime = mgr.status()
    running = bool(runtime.get("running"))
    scan = _ServerState.local_scan or {}
    return {
        "ok": True,
        "enabled": bool(settings.get("enabled")),
        "root": settings.get("root") or "",
        "server_path": settings.get("server_path") or "",
        "params": settings.get("params") or {},
        "models": scan.get("models", []) or [],
        "servers": scan.get("servers", []) or [],
        "managers": scan.get("managers", []) or [],
        # Модели, найденные НА ПК (удалённый скан через ПК-агент).
        "pc_models": (_ServerState.pc_scan or {}).get("models", []) or [],
        "pc_scan": _ServerState.pc_scan or {},
        # Туннель к ПК + активный движок (слабая/сильная).
        "tunnel": _tunnel_payload(),
        "engine": _ServerState.local_engine,
        "active_label": _active_label(),
        "weak_label": config.WEAK_LABEL,
        "strong_label": config.STRONG_LABEL,
        "running": running,
        "runtime": runtime,
    }


# ---------------------------------------------------------------------------
# МОДЕЛЬ ЭМБЕДДИНГОВ RAG (Ollama): reuse-first + скан/запуск (ПК или VPS)
# ---------------------------------------------------------------------------
def _embed_manager():
    """Лениво создаёт менеджер Ollama (singleton)."""
    if _ServerState.embed_manager is None:
        _ServerState.embed_manager = ollama_server.OllamaManager()
    return _ServerState.embed_manager


def _is_pc_path(path):
    """True, если путь похож на путь ПК (диск/UNC), а не на путь VPS."""
    p = str(path or "").strip()
    return bool(re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("\\\\"))


def _embed_base_url(url=None):
    """Базовый URL Ollama из эндпоинта эмбеддингов (/api/... или /v1/...)."""
    u = str(url or "").rstrip("/")
    for suffix in ("/api/embeddings", "/api/embed", "/v1/embeddings"):
        if u.endswith(suffix):
            return u[:-len(suffix)]
    return u


def _embed_url_for_source(source):
    """Эндпоинт эмбеддингов для источника: ПК — туннель, VPS — localhost."""
    if str(source or "").lower() == "pc":
        return ollama_server.tunnel_url() + "/api/embeddings"
    return ollama_server.server_url() + "/api/embeddings"


def _record_embed_selection(res, source, root="", exe="", models_dir=""):
    """Сохраняет активный движок эмбеддингов на ВПС (или локально)."""
    settings = ollama_server.load_settings()
    settings.update({
        "enabled": True,
        "managed": True,
        "backend": "ollama",
        "api": "ollama",
        "url": _embed_url_for_source(source),
        "model": str(res.get("model") or settings.get("model") or ""),
        "dim": int(res.get("dim") or settings.get("dim")
                   or config.RAG_EMBED_DIM),
        "source": "pc" if str(source).lower() == "pc" else "vps",
        "exe": exe or settings.get("exe") or "",
        "models_dir": models_dir or settings.get("models_dir") or "",
        "root": root or settings.get("root") or "",
    })
    ollama_server.save_settings(settings)
    return settings


def _embed_state_payload():
    """Снимок блока «Модель эмбеддингов» для UI."""
    mgr = _embed_manager()
    settings = mgr.load_settings()
    runtime = mgr.status()
    pc_status = None
    if str(settings.get("source") or "").lower() == "pc":
        agent = _agent_api("/embed/status")
        if agent.get("ok") or agent.get("alive"):
            pc_status = agent
            if agent.get("running"):
                runtime = agent
    scan = _ServerState.embed_scan or {}
    pc = _ServerState.pc_embed_scan or {}
    models = list(scan.get("models") or [])
    servers = list(scan.get("servers") or [])
    if not models and pc:
        models = list(pc.get("models") or [])
    if not servers and pc:
        servers = list(pc.get("servers") or [])
    active = ollama_server.active_embed_config()
    reuse_offer = None
    if runtime.get("running"):
        selected = str(active.get("model") or settings.get("model") or "")
        names = [m.get("name") for m in runtime.get("models") or []]
        reuse_offer = {
            "available": True,
            "external": bool(runtime.get("external")),
            "pid": runtime.get("pid"),
            "url": runtime.get("url"),
            "models": runtime.get("models") or [],
            "embedding_models": [m.get("name")
                                 for m in runtime.get("models") or []
                                 if m.get("embedding")],
            "selected_model_present": bool(
                selected and any(ollama_server.same_model(selected, n)
                                 for n in names)),
            "selected_model": selected,
            "loaded": [m.get("name")
                       for m in runtime.get("models") or []
                       if m.get("loaded")],
        }
    return {
        "ok": True,
        "settings": settings,
        "runtime": runtime,
        "pc_status": pc_status,
        "models": models,
        "servers": servers,
        "models_dir": (scan.get("models_dir") or pc.get("models_dir")
                       or settings.get("models_dir") or ""),
        "pc_models": (pc.get("models") or []),
        "pc_scan": pc,
        "source": "pc" if settings.get("source") == "pc" else "vps",
        "reuse_offer": reuse_offer,
        "active": active,
        "tunnel": {
            "host": config.EMBED_TUNNEL_HOST,
            "port": config.EMBED_TUNNEL_PORT,
            "url": ollama_server.tunnel_url(),
        },
    }


def _embed_scan_local(root):
    """Скан корня на VPS (Ollama установлена локально)."""
    mgr = _embed_manager()
    settings = mgr.load_settings()
    settings["root"] = str(root or settings.get("root") or "").strip()
    settings["source"] = "vps"
    mgr.save_settings(settings)
    result = mgr.scan(settings["root"])
    _ServerState.embed_scan = result
    _ServerState.pc_embed_scan = None
    return result


def _embed_scan_pc(root):
    """Скан корня НА ПК через служебный API агента."""
    root = str(root or config.EMBED_PC_ROOT or config.PC_ROOT or "").strip()
    res = _agent_api("/embed/scan", {"root": root}, method="POST",
                     timeout=60)
    if res.get("ok") or res.get("models") is not None:
        _ServerState.pc_embed_scan = {
            "root": res.get("root") or root,
            "models": res.get("models") or [],
            "servers": res.get("servers") or [],
            "models_dir": res.get("models_dir") or "",
            "errors": res.get("errors") or [],
        }
        _ServerState.embed_scan = None
        settings = _embed_manager().load_settings()
        settings["root"] = res.get("root") or root
        settings["source"] = "pc"
        _embed_manager().save_settings(settings)
    return res


def _wait_tunnel_embed(model=None, timeout=60):
    """Ждёт, пока Ollama ПК станет видна на VPS через туннель."""
    deadline = time.time() + int(timeout)
    last = {}
    while time.time() < deadline:
        last = ollama_server.probe(ollama_server.tunnel_url(), timeout=3)
        if last.get("alive"):
            names = [m.get("name") for m in last.get("models") or []]
            if not model or any(ollama_server.same_model(model, n)
                                for n in names):
                return last
        time.sleep(1.0)
    return last


def _sync_agent_local():
    """Синхронизирует состав источников агента с локальной моделью.

    Если локальная модель ВКЛЮЧЕНА (флаг enabled) — агент получает источник
    "local" с URL и именем модели (появляется в селекторе). Иначе источник
    убирается из состава агента.
    """
    agent = _ServerState.agent
    if agent is None or not hasattr(agent, "set_local_model"):
        return

    # Сильная модель (ПК) активна: не перетираем её слабой настройкой.
    if _ServerState.local_engine == "strong":
        probe = _tunnel_probe()
        models = probe.get("models") or []
        model_name = models[0] if models else config.STRONG_LABEL
        agent.set_local_model(url=local_server.tunnel_url(),
                              model=model_name,
                              label=config.STRONG_LABEL)
        return

    settings = _local_manager().load_settings()
    enabled = bool(settings.get("enabled"))
    mgr = _local_manager()
    runtime = mgr.status()
    running = runtime.get("running")
    if running is None:
        # Совместимость с менеджерами/моками без поля running.
        running = bool(mgr.is_running()) if hasattr(mgr, "is_running") else True
    running = bool(running)
    params = settings.get("params") or {}
    url = None
    model_name = None
    # Источник Local добавляем ТОЛЬКО если сервер реально готов.
    # Флаг enabled может остаться true после рестарта сервиса, но процесс
    # пользователь ещё не запустил — модель не должна быть доступна.
    if running:
        h = params.get("host") or config.LOCAL_HOST
        p = params.get("port") or config.LOCAL_PORT
        url = local_server.server_url(h, p)
        # Имя модели: сначала из /v1/models запущенного сервера, иначе из
        # параметров (basename .gguf без расширения), иначе метка по умолчанию.
        rmodels = runtime.get("models") or []
        if rmodels:
            model_name = rmodels[0]
        else:
            model_path = params.get("model") or ""
            model_name = (os.path.splitext(os.path.basename(model_path))[0]
                          if model_path else config.LOCAL_LABEL)
    agent.set_local_model(url=url, model=model_name,
                          label=config.LOCAL_LABEL)


def _running_local_label():
    """Метка локальной модели, если её сервер ЗАПУЩЕН и готов (иначе "").

    Используется для выбора локальной модели по умолчанию: как только сервер
    поднят через блок «Локальная модель», /help и чат работают через неё, без
    ручного выбора модели в селекторе.
    """
    try:
        if _ServerState.local_engine == "strong":
            probe = _tunnel_probe()
            if probe.get("alive"):
                return str(config.STRONG_LABEL or "Qwen-9B")
        mgr = _local_manager()
        status = mgr.status()
        running = status.get("running")
        if running is None:
            running = bool(mgr.is_running()) if hasattr(mgr, "is_running") else True
        if not running or not status.get("ready"):
            return ""
        return str(config.LOCAL_LABEL or "Local")
    except Exception as exc:                     # noqa: BLE001
        print("[LOCAL] не удалось определить статус: %s" % exc, flush=True)
        return ""

# ---------------------------------------------------------------------------
# ЛОКАЛЬНАЯ LLM С ПК ЧЕРЕЗ SSH-ТУННЕЛЬ (Задача 1)
# ---------------------------------------------------------------------------
def _active_label():
    """Имя АКТИВНОЙ модели для UI: слабая (VPS) или сильная (ПК)."""
    return (config.STRONG_LABEL if _ServerState.local_engine == "strong"
            else config.WEAK_LABEL)


def _initial_engine():
    """Движок при старте веб-сервера.

    Всегда слабая модель VPS: сильную и RAG пользователь запускает сам.
    """
    return "weak"


def _agent_api(path, payload=None, method="GET", timeout=None):
    """Обращается к служебному API ПК-агента (loopback VPS: AGENT_PORT)."""
    url = "http://%s:%d%s" % (config.AGENT_HOST, config.AGENT_PORT, path)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(
                req, timeout=timeout
                or getattr(config, "AGENT_TIMEOUT", 15)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"ok": False, "alive": False,
                "error": "агент ПК недоступен: %s" % exc}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "alive": False, "error": str(exc)}


def _tunnel_probe(timeout=None):
    """Проверяет живость туннеля к ПК (движок сильной модели)."""
    return local_server.probe_remote(local_server.tunnel_url(),
                                     timeout=timeout)


def _embed_probe(timeout=None):
    """Проверяет активный движок эмбеддингов (Ollama) — GET /api/tags."""
    cfg = ollama_server.active_embed_config()
    if str(cfg.get("backend") or "").lower() == "none":
        return {"alive": False, "models": [], "model_ready": False,
                "error": "эмбеддинги не запущены", "url": "",
                "model": cfg.get("model")}
    base = _embed_base_url(cfg.get("url")) or ollama_server.tunnel_url()
    res = ollama_server.probe(base, timeout=timeout)
    model = str(cfg.get("model") or "")
    names = [m.get("name") for m in res.get("models") or []]
    res["model_ready"] = bool(model and any(
        ollama_server.same_model(model, n) for n in names))
    res["model"] = model
    res["backend"] = cfg.get("backend")
    res["source"] = cfg.get("source")
    return res


def _rag_enabled_global():
    """Включён ли RAG в общих настройках (для выбора: опрашивать Ollama?)."""
    try:
        from rtk_app.rag import RagStore
        return bool(RagStore().load_settings().get("enabled"))
    except Exception:                              # noqa: BLE001
        return False


def _tunnel_payload(check_embed=None):
    """Состояние туннеля к ПК + эмбеддингов Ollama + активный движок."""
    probe = _tunnel_probe()
    agent = _agent_api("/confirm_status")
    agent_alive = bool(agent.get("ok") or agent.get("alive"))
    if check_embed is None:
        check_embed = _rag_enabled_global()
    # /status (движок + Ollama) спрашиваем только если агент реально ответил:
    # иначе каждый опрос UI ждал бы лишний таймаут. При выключенном RAG
    # просим агент НЕ опрашивать Ollama (?ollama=0).
    status_path = "/status?ollama=%d" % (1 if check_embed else 0)
    agent_status = _agent_api(status_path) if agent_alive else {}
    confirmed = bool(agent.get("confirmed")) if agent_alive else False
    if check_embed:
        embed = _embed_probe()
    else:
        # RAG выключен — не трогаем Ollama через туннель.
        embed = {"alive": False, "models": [], "model_ready": False,
                 "error": "RAG выключен",
                 "url": "http://%s:%d" % (config.EMBED_TUNNEL_HOST,
                                          config.EMBED_TUNNEL_PORT)}
    eng = _ServerState.local_engine
    return {
        "pc_alive": bool(probe.get("alive")),
        "pc_models": probe.get("models") or [],
        "pc_error": probe.get("error"),
        "confirmed": confirmed,
        "agent_alive": agent_alive,
        "agent_status": agent_status,
        "embed_alive": bool(embed.get("alive")),
        "embed_models": embed.get("models") or [],
        "embed_model_ready": bool(embed.get("model_ready")),
        "embed_model": embed.get("model") or config.RAG_EMBED_MODEL,
        "embed_source": embed.get("source"),
        "embed_backend": embed.get("backend"),
        "embed_url": embed.get("url"),
        "embed_error": embed.get("error"),
        "embed_port": config.EMBED_TUNNEL_PORT,
        "engine": eng,
        "active_label": _active_label(),
        "weak_label": config.WEAK_LABEL,
        "strong_label": config.STRONG_LABEL,
        "tunnel_host": config.TUNNEL_HOST,
        "tunnel_port": config.TUNNEL_PORT,
        "agent_port": config.AGENT_PORT,
    }


def _pair_payload():
    """Готовые команды запуска ПК-агента (для неспециалиста)."""
    vps = str(getattr(config, "TUNNEL_SSH", "") or "").strip()
    engine_port = int(getattr(config, "LOCAL_PORT", 8080) or 8080)
    tunnel_port = int(getattr(config, "TUNNEL_PORT", 18080) or 18080)
    agent_port = int(getattr(config, "AGENT_PORT", 18081) or 18081)
    ollama_port = int(getattr(config, "OLLAMA_PORT", 11434) or 11434)
    embed_port = int(getattr(config, "EMBED_TUNNEL_PORT", 11435) or 11435)
    identity = str(getattr(config, "TUNNEL_IDENTITY", "")
                   or "%USERPROFILE%\\.ssh\\rtk_pc")
    pc_root = str(getattr(config, "PC_ROOT", "") or "").strip()
    embed_root = str(getattr(config, "EMBED_PC_ROOT", "") or "").strip()
    vps_arg = vps or "rtk@<IP-этого-VPS>"
    root_arg = (" --root \"%s\"" % pc_root) if pc_root else ""
    embed_arg = (" --embed-root \"%s\"" % embed_root) if embed_root else ""
    command = (
        "python -m rtk_pc_agent --vps %s --identity \"%s\" "
        "--engine-port %d --tunnel-port %d --agent-port %d "
        "--ollama-port %d --embed-tunnel-port %d%s%s"
        % (vps_arg, identity, engine_port, tunnel_port, agent_port,
           ollama_port, embed_port, root_arg, embed_arg))
    script = (
        "$ErrorActionPreference = \"Stop\"\r\n"
        "python -m rtk_pc_agent `\r\n"
        "  --vps %s `\r\n"
        "  --identity \"%s\" `\r\n"
        "  --engine-port %d `\r\n"
        "  --tunnel-port %d `\r\n"
        "  --agent-port %d `\r\n"
        "  --ollama-port %d `\r\n"
        "  --embed-tunnel-port %d%s%s\r\n"
        % (vps_arg, identity, engine_port, tunnel_port, agent_port,
           ollama_port, embed_port,
           (" `\r\n  --root \"%s\"" % pc_root) if pc_root else "",
           (" `\r\n  --embed-root \"%s\"" % embed_root)
           if embed_root else ""))
    return {"vps": vps, "engine_port": engine_port, "tunnel_port": tunnel_port,
            "agent_port": agent_port, "ollama_port": ollama_port,
            "embed_port": embed_port, "identity": identity,
            "root": pc_root, "embed_root": embed_root,
            "command": command, "script": script}


def _activate_strong():
    """Включает СИЛЬНУЮ модель (ПК): источник local -> адрес туннеля."""
    probe = _tunnel_probe()
    if not probe.get("alive"):
        return {"ok": False,
                "error": "туннель к ПК недоступен: %s"
                         % (probe.get("error") or "нет ответа"),
                "engine": _ServerState.local_engine}
    models = probe.get("models") or []
    model_name = models[0] if models else config.STRONG_LABEL
    url = local_server.tunnel_url()
    agent = _ServerState.agent
    if agent is not None and hasattr(agent, "set_local_model"):
        agent.set_local_model(url=url, model=model_name,
                              label=config.STRONG_LABEL)
    _ServerState.local_engine = "strong"
    print("[TUNNEL] активна СИЛЬНАЯ модель: %s (%s, model=%r)"
          % (config.STRONG_LABEL, url, model_name), flush=True)
    return {"ok": True, "engine": "strong", "url": url,
            "model": model_name, "label": config.STRONG_LABEL}


def _activate_weak():
    """Возвращает СЛАБУЮ модель (VPS): источник local -> 127.0.0.1:8080."""
    _ServerState.local_engine = "weak"
    agent = _ServerState.agent
    if agent is None or not hasattr(agent, "set_local_model"):
        return {"ok": True, "engine": "weak", "label": config.WEAK_LABEL}
    settings = _local_manager().load_settings()
    params = settings.get("params") or {}
    h = params.get("host") or config.LOCAL_HOST
    p = params.get("port") or config.LOCAL_PORT
    url = local_server.server_url(h, p)
    runtime = _local_manager().status()
    if not runtime.get("ready"):
        # Пользователь ещё не запустил локальную модель VPS.
        agent.set_local_model(url=None, model=None,
                              label=config.WEAK_LABEL)
        return {"ok": False, "engine": "weak",
                "error": "локальная модель VPS не запущена — нажмите "
                         "«Запустить» в блоке «Локальная модель»"}
    rmodels = runtime.get("models") or []
    if rmodels:
        model_name = rmodels[0]
    else:
        model_path = params.get("model") or ""
        model_name = (os.path.splitext(os.path.basename(model_path))[0]
                      if model_path else config.WEAK_LABEL)
    label = config.LOCAL_LABEL or config.WEAK_LABEL
    agent.set_local_model(url=url, model=model_name, label=label)
    print("[TUNNEL] активна СЛАБАЯ модель: %s (%s, model=%r)"
          % (label, url, model_name), flush=True)
    return {"ok": True, "engine": "weak", "url": url,
            "model": model_name, "label": label}


def _model_selection_error(model, scan, field="path"):
    """Проверяет, что модель выбрана ПОСЛЕ сканирования.

    Возвращает текст ошибки или "" если всё в порядке. Нужна, чтобы запуск
    не подставлял «первую попавшуюся» модель: пользователь обязан сначала
    просканировать папку и выбрать модель вручную.
    """
    model = str(model or "").strip()
    if not model:
        return ("Сначала просканируйте папку и выберите модель "
                "(кнопка «Сканировать»).")
    items = (scan or {}).get("models") or []
    if not items:
        return ("Нет результатов сканирования — нажмите «Сканировать» "
                "и выберите модель.")
    values = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in (field, "path", "name"):
            val = str(item.get(key) or "").strip()
            if val:
                values.add(val)
    if model not in values:
        return ("Выбранная модель отсутствует в последнем скане — "
                "обновите скан и выберите модель.")
    return ""


def _next_engine_on_tick(alive, engine):
    """Политика фонового контроля туннеля.

    * обрыв туннеля при активной сильной -> слабая (безопасный возврат);
    * восстановление туннеля НЕ переключает на сильную, если не включено
      ``config.AUTO_STRONG``: выбор модели — за пользователем.
    """
    if not alive and engine == "strong":
        return "weak"
    if alive and engine == "weak" and getattr(config, "AUTO_STRONG", False):
        return "strong"
    return engine


def _tunnel_tick_loop(interval, stop_event):
    """Фоновый контроль туннеля: авто-возврат слабая <-> сильная."""
    print("[TUNNEL] фоновый контроль туннеля: каждые %d c." % interval,
          flush=True)
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break
        try:
            with _ServerState.tunnel_lock:
                probe = _tunnel_probe()
                alive = bool(probe.get("alive"))
                eng = _ServerState.local_engine
                next_eng = _next_engine_on_tick(alive, eng)
                if next_eng == "weak" and eng == "strong":
                    print("[TUNNEL] обрыв туннеля — авто-возврат на слабую "
                          "(%s)" % config.WEAK_LABEL, flush=True)
                    _activate_weak()
                elif next_eng == "strong" and eng == "weak":
                    print("[TUNNEL] восстановлен — переключение на сильную "
                          "(%s)" % config.STRONG_LABEL, flush=True)
                    _activate_strong()
        except Exception as exc:                   # noqa: BLE001
            print("[TUNNEL] ошибка контроля: %s" % exc, flush=True)
    print("[TUNNEL] фоновый контроль туннеля остановлен.", flush=True)


def _start_tunnel_tick():
    """Запускает фоновый поток контроля туннеля (если не запущен)."""
    interval = int(getattr(config, "TUNNEL_CHECK_PERIOD", 0) or 0)
    if interval <= 0:
        return
    if (_ServerState.tunnel_thread
            and _ServerState.tunnel_thread.is_alive()):
        return
    stop = threading.Event()
    _ServerState.tunnel_stop = stop
    th = threading.Thread(target=_tunnel_tick_loop, args=(interval, stop),
                          daemon=True)
    _ServerState.tunnel_thread = th
    th.start()


def _stop_tunnel_tick():
    """Останавливает фоновый поток контроля туннеля."""
    stop = _ServerState.tunnel_stop
    if stop is not None:
        stop.set()
    _ServerState.tunnel_thread = None
    _ServerState.tunnel_stop = None


def _mcp_state_payload(check=False):
    """Собирает состояние MCP для ответа клиенту.

    check=True — предварительно ПРОВЕРЯЕТ статус сервера (подключение +
    список инструментов) и обновляет сохранённый статус.
        """
    if check:
        _ServerState.mcp_status = _probe_mcp_status()
    status = _ServerState.mcp_status
    servers = []
    if mcp_client is not None:
        try:
            servers = list(mcp_client.mcp_servers())
        except Exception:
            servers = []
    # Режим «Авто» — не отдельный процесс, а вариант выбора в UI.
    auto_id = _mcp_auto_id()
    if not any(str(s.get("id")) == auto_id for s in servers):
        servers.insert(0, {
            "id": auto_id,
            "label": getattr(config, "MCP_AUTO_LABEL",
                             "Авто (модель выбирает сервер)"),
            "args": [],
        })
    return {
        "ok": True,
        "enabled": bool(_ServerState.mcp_enabled),
        "model": _ServerState.mcp_model or "",
        "server": _ServerState.mcp_server or "",
        "servers": servers,
        "available": mcp_client is not None,
        "status": status,
        # Устаревший ключ: композиция удалена, всегда False.
        "composition_running": bool(_ServerState.composition_running),
    }


def _probe_mcp_status():
    """Проверяет статус MCP-сервера (через mcp_client.mcp_status)."""
    if mcp_client is None:
        return {"ok": False, "connected": False, "tools_count": 0,
                "tools": [], "server": "",
                "error": "MCP-клиент недоступен (не установлен пакет mcp)."}
    try:
        if _mcp_is_auto():
            tools = []
            errors = []
            connected = []
            for srv in mcp_client.mcp_servers():
                sid = str(srv.get("id"))
                st = mcp_client.mcp_status(server_id=sid)
                if st.get("ok"):
                    connected.append(sid)
                    tools.extend("%s.%s" % (sid, name)
                                 for name in (st.get("tools") or []))
                else:
                    errors.append("%s: %s" % (sid, st.get("error") or "ошибка"))
            if not connected:
                return {"ok": False, "connected": False, "tools_count": 0,
                        "tools": [], "server": _mcp_auto_id(),
                        "error": "; ".join(errors) or "нет доступных серверов"}
            return {"ok": True, "connected": True,
                    "tools_count": len(tools), "tools": tools,
                    "server": "авто: " + ", ".join(connected),
                    "error": None}
        return mcp_client.mcp_status(server_id=(_ServerState.mcp_server or None))
    except Exception as exc:
        return {"ok": False, "connected": False, "tools_count": 0,
                "tools": [], "server": "", "error": str(exc)}


def _set_agent(agent):
    """Запоминает агента, который обслуживает все запросы."""
    _ServerState.agent = agent

def _ensure_session():
    """Лениво создаёт единственное хранилище сессии диалога."""
    if _ServerState.session is None:
        _ServerState.session = SessionStore()
    return _ServerState.session


def _file_key_value(path):
    """Первая значимая строка файла ключа ('' — файла нет/он пуст)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:                            # noqa: BLE001
        return ""
    return ""


def _keys_status_payload():
    """Статус ключей облачных моделей: наличие файлов и маски (без ключей)."""
    ds = _file_key_value(config.DS_KEY_FILE)
    gc = _file_key_value(config.GC_KEY_FILE)
    return {
        "ok": True,
        # Ключи из интерфейса живут ТОЛЬКО в памяти вкладки и на сервере
        # не сохраняются (см. rtk_app.runtime_keys).
        "session_only": True,
        "deepseek": {"file": bool(ds), "masked": runtime_keys.mask(ds)},
        "gigachat": {"file": bool(gc), "masked": runtime_keys.mask(gc)},
    }


class WebRequestHandler(BaseHTTPRequestHandler):
    server_version = "MultiModelChat/1.0"

    @property
    def agent(self):
        """Единый агент (rtk_app.agent.Agent), созданный при старте сервера."""
        return _ServerState.agent

    # Какая КЛИЕНТСКАЯ сессия обслуживает текущий запрос (cookie sid).
    _sid = None
    _session_obj = None
    _pending_sid = None
    _cookie_to_set = None
    _pending_owner_cookie = None

    @property
    def session(self):
        """Хранилище сессии ТЕКУЩЕГО клиента (или общая при выключенном лимите)."""
        if self._session_obj is not None:
            return self._session_obj
        return _ensure_session()

    def _client_ip(self):
        """IP клиента: из X-Forwarded-For (за nginx) или из сокета."""
        if getattr(config, "BEHIND_PROXY", False):
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
            xr = self.headers.get("X-Real-IP")
            if xr:
                return xr.strip()
        return self.client_address[0] if self.client_address else "?"

    def _rate_limited(self):
        """Rate limit по IP. True — запрос отклонён (429 уже отправлен)."""
        if not getattr(config, "RATE_LIMIT_PER_MIN", 0):
            return False
        allowed, retry = rate_limit.get_limiter().check(self._client_ip())
        if allowed:
            return False
        payload = json.dumps(
            {"ok": False,
             "error": "Слишком часто запросов. Повторите позже."},
            ensure_ascii=False).encode("utf-8")
        self.send_response(429)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Retry-After", str(retry))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except Exception:
            pass
        return True

    def _read_cookie(self, name):
        """Читает cookie по имени (или None)."""
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            kv = part.strip().split("=", 1)
            if len(kv) == 2 and kv[0].strip() == name:
                return kv[1].strip()
        return None

    def _read_sid_cookie(self):
        return self._read_cookie("sid")

    def _is_owner_request(self):
        """Признак владельца по секретному токену (URL/cookie/заголовок)."""
        want = str(getattr(config, "OWNER_TOKEN", "") or "").strip()
        if not want:
            return (False, None)
        import urllib.parse
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        url_tok = (q.get("owner", [""])[0] or "").strip()
        if url_tok:
            return (url_tok == want, url_tok if url_tok == want else None)
        cookie_tok = (self._read_cookie(
            getattr(config, "OWNER_COOKIE", "owner")) or "").strip()
        if cookie_tok:
            return (cookie_tok == want, None)
        hdr = (self.headers.get("X-Owner-Token") or "").strip()
        if hdr:
            return (hdr == want, None)
        return (False, None)

    def _resolve_session(self, is_activity=True, allow_new=True):
        """Привязывает запрос к клиентской сессии (cookie sid)."""
        if not getattr(config, "MAX_SESSIONS", 0):
            self._session_obj = None
            return "ok"
        is_owner, url_tok = self._is_owner_request()
        if url_tok and is_owner:
            self._pending_owner_cookie = url_tok
        sid = self._read_sid_cookie()
        if not allow_new and not sid:
            self._session_obj = None
            return "ok"
        mgr = _ServerState._RTK_VPS_SESSIONS
        kind, sess = mgr.touch(sid, is_activity=is_activity,
                               privileged=is_owner)
        if kind in ("ok", "new") and sess is not None:
            if kind == "new":
                self._sid = sess.sid
                self._pending_sid = sess.sid
            else:
                self._sid = sid
            self._session_obj = sess.store
            return "ok"
        self._sid = sid
        self._session_obj = None
        return kind

    def _set_sid_cookie(self, sid):
        self.send_header(
            "Set-Cookie",
            "sid=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400" % sid)

    def _set_owner_cookie(self, token):
        self.send_header(
            "Set-Cookie",
            "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000"
            % (getattr(config, "OWNER_COOKIE", "owner"), token))

    def _send_full(self):
        """Заглушка «нет свободной сессии» (HTTP 503)."""
        body = (
            "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<meta http-equiv='refresh' content='15'>"
            "<title>Нет свободной сессии</title>"
            "<style>body{font-family:sans-serif;background:#1e1e1e;color:#eee;"
            "display:flex;align-items:center;justify-content:center;"
            "height:100vh;margin:0}div{max-width:440px;text-align:center;"
            "padding:24px}a{color:#6cf}</style></head><body><div>"
            "<h2>Сейчас занято</h2>"
            "<p>Сервис ограничен (не более %d одновременных сессий). "
            "Все места заняты — освободится через минуту или раньше.</p>"
            "<p>Страница обновится автоматически. "
            "<a href='/'>Повторить сейчас</a></p>"
            "</div></body></html>"
        ) % int(getattr(config, "MAX_SESSIONS", 2) or 2)
        payload = body.encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Retry-After", "15")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except Exception:
            pass

    def _send_bytes(self, status, body, content_type, set_sid=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        if set_sid:
            self._set_sid_cookie(set_sid)
        elif self._cookie_to_set:
            self._set_sid_cookie(self._cookie_to_set)
            self._cookie_to_set = None
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, obj, set_sid=None):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, payload, "application/json; charset=utf-8",
                         set_sid=set_sid)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    # ---------------- GET ----------------
    def do_GET(self):
        # Привязка к клиентской сессии (cookie sid). /api/health и статика
        # доступны всегда; прочие /api/* без слота получают 503.
        import urllib.parse
        p = urllib.parse.urlparse(self.path).path
        if p.startswith("/api/") and p != "/api/health":
            # /api/heartbeat — «тихий» опрос: не тратит квоту rate limit.
            if p != "/api/heartbeat" and self._rate_limited():
                return
            is_act = (p != "/api/heartbeat")
            kind = self._resolve_session(is_activity=is_act, allow_new=True)
            if kind == "full":
                return self._send_json(503, {"ok": False,
                    "error": "Нет свободной сессии.", "state": "full"})
        if p == "/":
            kind = self._resolve_session(is_activity=True, allow_new=True)
            if kind == "full":
                return self._send_full()
        return self._do_GET()

    def _do_GET(self):
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/", "/index.html"):
            # Токен владельца пришёл в URL (?owner=...): запомним в cookie и
            # очистим адресную строку, чтобы секрет не оставался в истории.
            if self._pending_owner_cookie:
                self.send_response(302)
                self.send_header("Location", "/")
                self._set_owner_cookie(self._pending_owner_cookie)
                self._pending_owner_cookie = None
                if self._pending_sid:
                    self._set_sid_cookie(self._pending_sid)
                    self._pending_sid = None
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self._pending_sid:
                self._cookie_to_set = self._pending_sid
                self._pending_sid = None
            return self._serve_static_safe("index.html")
        if path.startswith(("/css/", "/js/")):
            return self._serve_static_safe(path.lstrip("/"))
        if path.startswith("/docs/"):
            # Отдача документа из папки RAG (PDF, .md, исходник) по пути —
            # для ссылок «Источники» в карточке ответа (см.
            # agent._render_sources_html).
            return self._serve_rag_doc(path[len("/docs/"):])
        if path.startswith("/images/"):
            # Отдача ИЗОБРАЖЕНИЯ из хранилища RAG (встроенные картинки PDF) —
            # для блока «Фрагмент документа» в карточке ответа.
            return self._serve_rag_image(path[len("/images/"):])
        if path == "/api/health":
            info = _ServerState._RTK_VPS_SESSIONS.info()
            return self._send_json(200, {
                "ok": True, "service": "rtk-web",
                "sessions": info,
                "agent": bool(self.agent),
            })
        if path == "/api/heartbeat":
            hb = _ServerState._RTK_VPS_SESSIONS.heartbeat(self._sid)
            return self._send_json(200, {"ok": True, "session": hb})
        if path == "/api/keys":
            # Только наличие файлов и маски: полные ключи не отдаём никогда.
            return self._send_json(200, _keys_status_payload())
        if path == "/api/model":
            return self._send_json(200, {
                "ok": True,
                "model": self.agent.label if self.agent else config.MODEL,
                "models": [m["label"] for m in
                           (self.agent.available() if self.agent else [])],
                "available": (self.agent.available() if self.agent else []),
            })
        if path == "/api/session":
            # Единый снимок состояния сессии (session.full_state()) — те же
            # поля, что и в ответе /api/ask (см. §1.4 обзора оптимизаций).
            state = {"ok": True, "has_history": self.session.has_history()}
            state.update(self.session.full_state())
            return self._send_json(200, state)
        if path == "/api/profiles":
            state = self.session.profiles_state()
            return self._send_json(200, {
                "ok": True,
                "profiles": state["profiles"],
                "active": state["active"],
            })
        if path == "/api/invariants":
            return self._send_json(200, self.session.invariants_state())
        if path == "/api/mcp":
            return self._send_json(200, _mcp_state_payload(check=False))
        if path == "/api/rag":
            # Состояние RAG: настройки (enabled/docs_dir/top_k) + статистика
            # индекса (файлов, чанков, модель эмбеддингов, ошибки) +
            # состояние туннеля к Ollama ПК.
            payload = self.session.rag_status()
            if payload.get("enabled"):
                probe = _embed_probe()
                payload["embed_tunnel"] = {
                    "alive": bool(probe.get("alive")),
                    "models": probe.get("models") or [],
                    "model_ready": bool(probe.get("model_ready")),
                    "error": probe.get("error"),
                    "url": probe.get("url"),
                    "port": config.EMBED_TUNNEL_PORT,
                }
            else:
                # RAG выключен — Ollama не опрашиваем вообще.
                payload["embed_tunnel"] = {
                    "alive": False, "models": [], "model_ready": False,
                    "error": "RAG выключен",
                    "url": "http://%s:%d" % (config.EMBED_TUNNEL_HOST,
                                             config.EMBED_TUNNEL_PORT),
                    "port": config.EMBED_TUNNEL_PORT,
                }
            return self._send_json(200, payload)
        if path == "/api/local":
            # Состояние блока ЛОКАЛЬНОЙ модели (llama.cpp/Ollama): настройки,
            # найденные модели/серверы, состояние процесса, URL сервера.
            return self._send_json(200, _local_status_payload())
        if path == "/api/embed":
            # Состояние блока «Модель эмбеддингов RAG» (Ollama): скан,
            # reuse-предложение, активный движок, загруженные модели.
            return self._send_json(200, _embed_state_payload())
        self._send_json(404, {"ok": False, "error": "Not Found"})

    def do_POST(self):
        import urllib.parse
        if self._rate_limited():
            return
        path = urllib.parse.urlparse(self.path).path
        # /api/newchat обрабатывается ОСОБО: он должен работать даже когда
        # текущая сессия offline (кнопка «Начать новую сессию»), ротация
        # слота выполняется внутри _handle_newchat.
        if path == "/api/newchat":
            return self._handle_newchat()
        if path == "/api/newslot":
            return self._handle_newchat(force_renew=True)
        kind = self._resolve_session(is_activity=True, allow_new=True)
        if kind == "full":
            return self._send_json(503, {"ok": False,
                "error": "Нет свободной сессии.", "state": "full"})
        if kind == "offline":
            return self._send_json(409, {"ok": False,
                "error": "Сессия завершена. Откройте новую.",
                "state": "offline"})
        try:
            return self._do_POST()
        finally:
            # Ключи, введённые пользователем в интерфейсе, действуют ТОЛЬКО
            # на время этого запроса и нигде не сохраняются.
            runtime_keys.clear()

    def _do_POST(self):
        import urllib.parse
        if urllib.parse.urlparse(self.path).path == "/api/ask":
            return self._handle_ask()
        if urllib.parse.urlparse(self.path).path == "/api/keys":
            return self._handle_keys()
        if urllib.parse.urlparse(self.path).path == "/api/newchat":
            self.session.reset()
            return self._send_json(200, {"ok": True,
                                         "messages": self.session.snapshot()})
        if urllib.parse.urlparse(self.path).path == "/api/compact":
            return self._handle_compact()
        if urllib.parse.urlparse(self.path).path == "/api/compact_summary":
            return self._handle_compact_summary()
        if urllib.parse.urlparse(self.path).path == "/api/strategy":
            return self._handle_strategy()
        if urllib.parse.urlparse(self.path).path == "/api/facts":
            return self._handle_facts()
        if urllib.parse.urlparse(self.path).path == "/api/memory":
            return self._handle_memory()
        if urllib.parse.urlparse(self.path).path == "/api/branches":
            return self._handle_branches()
        if urllib.parse.urlparse(self.path).path == "/api/task":
            return self._handle_task()
        if urllib.parse.urlparse(self.path).path == "/api/invariants":
            return self._handle_invariants()
        if urllib.parse.urlparse(self.path).path == "/api/profiles":
            return self._handle_profiles()
        if urllib.parse.urlparse(self.path).path == "/api/mcp":
            return self._handle_mcp()
        if urllib.parse.urlparse(self.path).path == "/api/rag":
            return self._handle_rag()
        if urllib.parse.urlparse(self.path).path == "/api/local":
            return self._handle_local()
        if urllib.parse.urlparse(self.path).path == "/api/embed":
            return self._handle_embed()
        self._send_json(404, {"ok": False, "error": "Not Found"})

    # ---------------- Статика ----------------
    def _serve_static_safe(self, rel):
        root_real = os.path.realpath(config.WEB_ROOT)
        full = os.path.realpath(os.path.join(root_real, os.path.normpath(rel)))
        if not (full.startswith(root_real + os.sep) or full == root_real):
            return self._send_bytes(403, "Forbidden", "text/plain")
        if not os.path.isfile(full):
            return self._send_bytes(404, "Not Found", "text/plain")
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as f:
            body = f.read()
        # _send_bytes сам выставит Set-Cookie sid для новой сессии.
        self._send_bytes(200, body, ctype or "application/octet-stream")

    def _serve_rag_doc(self, rel):
        """Отдаёт PDF из папки RAG-документов по имени файла.

        Используется ссылками «Источники» в карточке ответа: имя файла
        совпадает с basename документа в индексе. Защита от выхода за
        пределы папки документов (path traversal): путь приводится к
        реальному и проверяется, что он лежит внутри docs_dir.
        """
        import urllib.parse
        rel = urllib.parse.unquote(rel or "").strip().lstrip("/")
        docs_dir = (self.session.rag_settings().get("docs_dir")
                    or config.RAG_DOCS_DIR)
        root_real = os.path.realpath(docs_dir)
        # Имя файла может содержать подпапки, но не может выйти за корень.
        full = os.path.realpath(os.path.join(root_real, os.path.normpath(rel)))
        if not (full.startswith(root_real + os.sep) or full == root_real):
            return self._send_bytes(403, "Forbidden", "text/plain")
        # Данные поддержки (users.json/tickets.json) не отдаём через /docs/:
        # даже если папка лежит внутри RAG-корня, это персональные данные.
        try:
            support_data_root = os.path.realpath(support_mod.data_dir())
            if (full == support_data_root
                    or full.startswith(support_data_root + os.sep)):
                return self._send_bytes(403, "Forbidden", "text/plain")
        except Exception:                        # noqa: BLE001
            pass
        if not os.path.isfile(full):
            # FAQ поддержки может лежать ВНЕ папки RAG-индекса (docs/support
            # при docs_dir=docs_rag): ищем файл по имени в каталоге поддержки,
            # не выходя за его пределы.
            support_root = os.path.realpath(support_mod.docs_dir())
            cand = os.path.realpath(os.path.join(
                support_root, os.path.basename(os.path.normpath(rel))))
            if ((cand.startswith(support_root + os.sep) or cand == support_root)
                    and os.path.isfile(cand)):
                full = cand
            else:
                return self._send_bytes(404, "Not Found", "text/plain")
        ctype, _ = mimetypes.guess_type(full)
        # Текстовые источники (README.md, docs/*.md, исходники проекта)
        # отдаём как text/plain с UTF-8: браузер покажет содержимое, а не
        # скачает файл.
        text_exts = (".md", ".mdx", ".rst", ".txt", ".py", ".js", ".css",
                     ".json", ".yml", ".yaml", ".toml", ".html", ".htm")
        if str(full).lower().endswith(text_exts):
            ctype = "text/plain; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Disposition",
            "inline; filename*=UTF-8''%s" % urllib.parse.quote(
                os.path.basename(full)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_rag_image(self, rel):
        """Отдаёт изображение из хранилища RAG (встроенные картинки PDF).

        rel — путь относительно RAG_IMAGES_DIR (как в images_for:
        "<doc_hash>/<sha>.png"). Защита от path traversal выполняется
        через RagStore.image_abs_path (реальный путь внутри хранилища).
        """
        import urllib.parse
        rel = urllib.parse.unquote(rel or "").strip().lstrip("/")
        try:
            full = self.session.rag.image_abs_path(rel)
        except Exception:                        # noqa: BLE001
            full = None
        if not full or not os.path.isfile(full):
            return self._send_bytes(404, "Not Found", "text/plain")
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------------- Обработчики: делегируют всю работу агенту ----------------

    def _maybe_auto_compact(self):
        """Инкрементальное сжатие истории: дописывает вытесненные сообщения.

        Логика: как только история становится больше keep, каждое новое
        вытесненное сообщение ДОПИСЫВАЕТСЯ в summary (Вариант A). Для keep = 5
        сжатие начинается с 6-го сообщения и продолжается по мере вытеснения.
        Summary хранится ОТДЕЛЬНО (compact.summary) и подставляется в
        следующий запрос вместо вытесненной части истории.
        """
        try:
            if not self.session.should_auto_compact():
                return
            head, end = self.session.head_to_compact()
            if not head:
                return
            keep = self.session.get_compact().get("keep", config.COMPACT_KEEP)
            prev_summary = self.session.get_compact().get("summary", "")
            # Дописываем вытесненную часть в существующее summary.
            summary = self.agent.compact_update(prev_summary, head)
            if summary:
                # Сохраняем summary и новую границу: сообщения [0:end) покрыты.
                self.session.apply_summary(summary, upto=end, keep=keep)
                print("[COMPACT] сжатие: +%d сообщ. -> summary (upto=%d)"
                      % (len(head), end), flush=True)
        except Exception as exc:
            # Сжатие не должно ломать основной запрос.
            print("[COMPACT] сжатие не удалось: %s" % exc, flush=True)

    def _help_payload(self, question, git_note, rag_hits, docs_dir=None):
        """Детерминированный ответ на /help (БЕЗ вызова LLM).

        Возвращает текст со сведениями о проекте: git-ветка (MCP), структура
        каталогов, состояние RAG-индекса и найденные фрагменты документации
        и кода. Используется, когда модель/персона не выбраны.
        """
        from rtk_app import rag as rag_mod
        lines = ["/help — справка о проекте (модель не вызывалась)", ""]
        lines.append("Вопрос: %s" % question)
        lines.append("")
        if git_note:
            lines.append("Git:")
            lines.append(git_note)
            lines.append("")
        brief = project_brief(docs_dir)
        if brief:
            lines.append("Структура проекта:")
            lines.append(brief)
            lines.append("")
        try:
            status = self.session.rag_status()
        except Exception:                            # noqa: BLE001
            status = {}
        lines.append("База знаний RAG: файлов %s, фрагментов %s (индекс %s)."
                     % (status.get("files", 0), status.get("chunks", 0),
                        "доступен" if status.get("available") else "пуст"))
        if rag_hits:
            lines.append("")
            lines.append("Найденные фрагменты документации/кода:")
            for h in list(rag_hits)[:_HELP_HITS_PREVIEW]:
                cid = rag_mod.format_citation_id(h.get("file"), h.get("page"),
                                                 h.get("chunk"))
                text = " ".join(str(h.get("text") or "").split())
                if len(text) > _HELP_HIT_CHARS:
                    text = text[:_HELP_HIT_CHARS] + "…"
                lines.append("- %s (score %.3f): %s"
                             % (cid or os.path.basename(
                                 str(h.get("file") or "")),
                                float(h.get("score") or 0), text))
        else:
            lines.append("")
            lines.append("Фрагментов по вопросу не найдено — уточните вопрос "
                         "или переиндексируйте папку с документацией.")
        lines.append("")
        lines.append("Подсказка: выберите модель или создайте персону — тогда "
                     "/help ответит развёрнуто с опорой на найденный контекст.")
        return {
            "ok": True,
            "help": True,
            "text": "\n".join(lines),
            "html": "",
            "answers": [],
            "usage": None,
            "context": {},
            "rag_sources": [],
            "rag_fragments": [],
            "rag_low_relevance": False,
        }

    def _review_payload(self, spec, model_label, repo_dir):
        """AI-ревью PR/диффа для команды /review (результат — в чат).

        spec — {base, head, use_rag} из parse_review();
        model_label — выбранная модель/персона/локальная;
        repo_dir — репозиторий (обычно docs_dir из настроек RAG).
        """
        from rtk_app import pr_review, html_report
        max_diff_chars = pr_review.MAX_DIFF_CHARS
        ctx = 8192
        local_alias = ""
        try:
            with open(config.LOCAL_SETTINGS_FILE, encoding="utf-8") as fh:
                settings = json.load(fh)
            params = settings.get("params") or {}
            ctx = int(params.get("ctx") or ctx)
            local_alias = str(params.get("alias") or "").strip()
        except Exception:                        # noqa: BLE001
            pass
        local_labels = {"Local",
                        str(getattr(config, "LOCAL_LABEL", "Local") or "Local")}
        running_label = _running_local_label()
        if running_label:
            local_labels.add(str(running_label))
        if local_alias:
            local_labels.add(local_alias)
        if model_label and model_label in local_labels:
            # Локальный llama-server с ctx 8192 не примет весь большой diff.
            max_diff_chars = pr_review.local_review_diff_limit(
                ctx_tokens=ctx, max_tokens=4000)
        result = pr_review.review_changes(
            repo=repo_dir,
            base=(spec or {}).get("base"),
            head=(spec or {}).get("head"),
            model=model_label,
            use_rag=bool((spec or {}).get("use_rag")),
            max_tokens=4000,
            max_diff_chars=max_diff_chars)
        review = result.get("review") or ""
        if not review:
            error = " ".join(str(result.get("error") or "неизвестная ошибка")
                             .split())
            review = ("## AI-ревью PR\n\n_Ревью не выполнено: %s_" % error)
        try:
            html = html_report.text_to_html_paragraphs(review)
        except Exception:                        # noqa: BLE001
            html = ""
        label = model_label or "review"
        return {
            "ok": bool(result.get("ok")),
            "review": True,
            "text": review,
            "html": html,
            "answers": [{"label": label, "model": label, "text": review,
                         "temperature": None}],
            "files": result.get("files") or [],
            "stat": result.get("stat") or "",
            "rag_used": bool(result.get("rag_used")),
            "rag_hits": result.get("rag_hits"),
            "diff_truncated": result.get("diff_truncated"),
            "usage": result.get("usage"),
            "error": result.get("error"),
        }

    def _files_payload(self, spec, model_label, root):
        """Выполняет сценарий /files и возвращает payload для чата."""
        from rtk_app import html_report
        scenario = str((spec or {}).get("scenario") or "help")
        if scenario == "help":
            result = {
                "ok": True, "scenario": "help", "error": None,
                "text": "\n".join([
                    "## Работа с файлами",
                    "",
                    "`/files find <запрос>` — найти использования по файлам.",
                    "`/files docs [base] [head] [--apply]` — обновить docs по diff.",
                    "`/files new <adr|changelog|readme> <тема> [--apply]` — создать файл.",
                    "`/files edit [путь] <инструкция> [--apply]` — изменить файл (путь можно не указывать).",
                    "",
                    "По умолчанию показывается diff; запись — с `--apply`.",
                ]),
                "files": [], "changed": [], "diff": "", "applied": False,
                "rag_used": False, "usage": None,
            }
        elif scenario == "find":
            result = file_tasks.run_find(
                root, (spec or {}).get("query") or "",
                max_results=getattr(config, "FILES_MAX_RESULTS", 200))
        elif scenario in ("docs", "new", "edit"):
            if not model_label:
                return {
                    "ok": False, "files_task": True, "scenario": scenario,
                    "text": "## Файлы\n\n_Выберите модель — без неё нельзя "
                            "сгенерировать изменения._",
                    "html": "", "answers": [], "files": [], "changed": [],
                    "diff": "", "applied": False, "rag_used": False,
                    "usage": None, "error": "не выбрана модель",
                }
            if scenario == "edit":
                result = file_tasks.run_edit_file(
                    root, (spec or {}).get("path") or "",
                    (spec or {}).get("instruction") or "",
                    model=model_label,
                    apply=bool((spec or {}).get("apply")))
            elif scenario == "docs":
                result = file_tasks.run_update_docs(
                    root, base=(spec or {}).get("base") or "HEAD~1",
                    head=(spec or {}).get("head") or "HEAD",
                    model=model_label,
                    apply=bool((spec or {}).get("apply")),
                    use_rag=bool((spec or {}).get("use_rag", True)))
            else:
                result = file_tasks.run_generate_file(
                    root, kind=(spec or {}).get("kind") or "adr",
                    topic=(spec or {}).get("topic") or "",
                    model=model_label,
                    apply=bool((spec or {}).get("apply")),
                    use_rag=bool((spec or {}).get("use_rag", True)),
                    path=(spec or {}).get("path") or "")
        else:
            result = {"ok": False, "scenario": scenario, "text": "",
                      "error": "неизвестный сценарий /files"}
        text = str(result.get("text") or "")
        if not text:
            text = "## Файлы\n\n_%s_" % (result.get("error") or
                                         "операция не выполнена")
        try:
            html = html_report.text_to_html_paragraphs(text)
        except Exception:                        # noqa: BLE001
            html = ""
        label = model_label or "files"
        answers = []
        if result.get("ok") and scenario in ("docs", "new", "edit"):
            answers = [{"label": label, "model": label, "text": text,
                        "temperature": None}]
        return {
            "ok": bool(result.get("ok")),
            "files_task": True,
            "scenario": scenario,
            "text": text,
            "html": html,
            "answers": answers,
            "files": result.get("files") or [],
            "input_files": result.get("input_files") or [],
            "changed": result.get("changed") or [],
            "diff": result.get("diff") or "",
            "applied": bool(result.get("applied")),
            "written": result.get("written") or [],
            "rag_used": bool(result.get("rag_used")),
            "usage": result.get("usage"),
            "error": result.get("error"),
        }

    @staticmethod
    def _apply_session_keys(data):
        """Кладёт ключи из тела запроса в ТЕКУЩИЙ поток (только на запрос).

        Ключи нигде не сохраняются: их стирает do_POST в finally. Некорректные
        значения игнорируются — модель тогда сообщит, что ключ не задан.
        """
        keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(keys, dict):
            runtime_keys.clear()
            return
        try:
            runtime_keys.set_keys(deepseek=keys.get("deepseek"),
                                  gigachat=keys.get("gigachat"))
        except (TypeError, ValueError) as exc:
            runtime_keys.clear()
            print("[KEYS] ключи из запроса не приняты: %s" % exc, flush=True)

    def _handle_keys(self):
        """GET/POST /api/keys — статус файлов и проверка вставленного ключа.

        Ключ, присланный в "check", никуда не сохраняется: используется
        только для одного пробного запроса к API.
        """
        data = self._read_json_body() or {}
        action = str(data.get("action") or "status").strip().lower()
        if action == "status":
            return self._send_json(200, _keys_status_payload())
        if action != "check":
            return self._send_json(400, {
                "ok": False, "error": "Неизвестное действие: %s" % action})
        provider = str(data.get("provider") or "").strip().lower()
        if provider not in ("deepseek", "gigachat"):
            return self._send_json(400, {
                "ok": False,
                "error": "provider: ожидается 'deepseek' или 'gigachat'"})
        from rtk_app import key_check
        try:
            result = key_check.check_key(provider, data.get("key"))
        except Exception as exc:                 # noqa: BLE001
            return self._send_json(200, {
                "ok": False, "provider": provider, "status": "invalid",
                "detail": str(exc)[:300], "hint": "", "masked": ""})
        result["provider"] = provider
        return self._send_json(200, result)

    def _handle_newchat(self, force_renew=False):
        """POST /api/newchat — новый разговор.

        renew=true (или /api/newslot) — выдать НОВЫЙ cookie sid и освободить
        прежний слот (кнопка «Начать новую сессию», обновление страницы).
        """
        data = self._read_json_body() or {}
        renew = force_renew or bool(data.get("renew"))
        if not renew or not getattr(config, "MAX_SESSIONS", 0):
            self.session.reset()
            return self._send_json(200, {"ok": True,
                                         "messages": self.session.snapshot()})
        is_owner, _ = self._is_owner_request()
        mgr = _ServerState._RTK_VPS_SESSIONS
        old_sid = self._read_sid_cookie()
        kind, sid = mgr.new_slot(old_sid=old_sid, privileged=is_owner)
        if kind != "new" or not sid:
            return self._send_json(503, {"ok": False,
                "error": "Нет свободной сессии.", "state": "full"})
        self._sid = sid
        self._session_obj = mgr.store_for(sid)
        self.session.reset()
        return self._send_json(200, {"ok": True, "renewed": True,
                                     "messages": self.session.snapshot()},
                               set_sid=sid)

    def _handle_ask(self):
        """Принимает запрос пользователя и передаёт его агенту.

        История диалога хранится на сервере (папка session/) и подхватывается
        при каждом запуске. Сервер передаёт агенту историю, выбор моделей и
        температуру, а после успешного ответа добавляет ход в сессию и
        сохраняет её на диск.
        """
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        # Ключи облачных моделей, введённые в браузере: действуют только
        # на время этого запроса (ничего не сохраняется).
        self._apply_session_keys(data)
        question = str(data.get("question", "")).strip()
        # Выбор моделей пользователем: [{"label": "…", "temperature": 0.7}]
        selected = data.get("models")
        if not isinstance(selected, list) or not selected:
            selected = None

        # КОМАНДА /help (docs/Final_task.md, задача 1): ассистент разработчика.
        # Вопрос после /help ищется по README/docs/коду (RAG) с добавлением
        # сведений о git-ветке (MCP «Проект»). Без выбранной модели/персоны
        # возвращается детерминированная справка — без вызова LLM.
        help_query = parse_help(question)
        is_help = help_query is not None
        if is_help:
            question = help_query or _HELP_DEFAULT_QUESTION

        # КОМАНДА /review (docs/Final_task.md, задача 2): AI-ревью PR/диффа
        # прямо в чате. Репозиторий — docs_dir, модель — выбранная/персона/
        # локальная. Команда не перехватывается MCP/«Доской».
        review_spec = parse_review(question)
        is_review = review_spec is not None

        # КОМАНДА /support (docs/Final_task.md, задача 3): ответ поддержки
        # с учётом тикета/пользователя (MCP support) и FAQ (RAG).
        support_spec = parse_support(question)
        is_support = support_spec is not None

        # КОМАНДА /files (docs/Final_task.md, задача 4): работа с файлами
        # проекта — поиск, обновление docs, генерация ADR/CHANGELOG.
        files_spec = parse_files(question)
        is_files = files_spec is not None

        # MCP работает только через выбранный сервер при включённом чекбоксе.
        via_mcp = bool(_ServerState.mcp_enabled)
        # ВАЖНО: /help, /review, /support и /files — ЯВНЫЕ команды. Они не
        # должны перехватываться MCP-режимом: иначе вместо справки/ревью
        # вернётся таблица MCP-сервера.
        if is_help or is_review or is_support or is_files:
            via_mcp = False

        # РЕЖИМ ОТВЕТА зависит от наличия персон:
        #   * ЕСТЬ активная персона — отвечает ПЕРСОНА, используя СВОЮ модель
        #     (выбор моделей сверху недоступен, но если клиент всё же прислал
        #     список — модель персоны имеет приоритет);
        #   * НЕТ персон — диалог идёт напрямую с ВЫБРАННЫМИ моделями.
        # ВАЖНО: при запросе через MCP (в т.ч. к «Доске») модель берётся из
        # селекта «модель MCP», а не из персон/моделей сверху.
        pid, pname, pmodel, pchar, pstyle = self.session.active_profile_attrs()
        if pid and not via_mcp:
            # Отвечает персона. Заголовок блока — имя персоны.
            answer_title = pname or "Персона"
            profile = {"name": pname, "character": pchar, "style": pstyle}
            # Модель персоны имеет приоритет; если у персоны модель не задана —
            # используем выбранные сверху модели, а если и их нет — все
            # доступные модели агента (передаём None).
            if pmodel:
                selected = [{"label": pmodel}]
            elif selected is None:
                selected = None  # агент опросит все доступные модели
        else:
            # Персон нет (или запрос идёт через MCP) — режим «напрямую».
            answer_title = None
            profile = None
            # ЛОКАЛЬНАЯ МОДЕЛЬ ПО УМОЛЧАНИЮ: если её сервер запущен через блок
            # «Локальная модель», а пользователь не выбрал модель/персону и не
            # включил MCP — используем локальную (и для /help, и для чата).
            if selected is None and not via_mcp:
                local_label = _running_local_label()
                if local_label:
                    try:
                        _sync_agent_local()
                    except Exception as exc:         # noqa: BLE001
                        print("[LOCAL] синхронизация агента: %s" % exc,
                              flush=True)
                    selected = [{"label": local_label}]
                    print("[LOCAL] по умолчанию используется %r (%s)"
                          % (local_label, "запущена"), flush=True)
            if (selected is None and not via_mcp and not is_help
                    and not is_review and not is_files):
                return self._send_json(200, {
                    "ok": False,
                    "error": "Не выбрана ни одна модель. Включите модель для "
                             "запроса.",
                    "text": "", "html": "", "answers": [],
                })

        # /review: AI-ревью PR/диффа прямо в чате (модель обязательна).
        if is_review:
            model_label = ""
            if selected:
                model_label = str((selected[0] or {}).get("label")
                                  or "").strip()
            if not model_label:
                model_label = _running_local_label()
            if not model_label:
                return self._send_json(200, {
                    "ok": False, "review": True,
                    "error": "Выберите модель (или запустите локальную) — "
                             "иначе /review не сможет выполнить ревью.",
                    "text": "", "html": "", "answers": [],
                })
            repo_dir = (self.session.rag_settings().get("docs_dir")
                        or config.RAG_DOCS_DIR)
            print("[REVIEW] %s..%s repo=%r model=%r"
                  % (review_spec["base"], review_spec["head"], repo_dir,
                     model_label), flush=True)
            return self._send_json(200, self._review_payload(
                review_spec, model_label, repo_dir))

        # /files: работа с файлами проекта. Поиск работает без модели;
        # docs/new требуют выбранную модель (или запущенную локальную).
        if is_files:
            model_label = ""
            if selected:
                model_label = str((selected[0] or {}).get("label")
                                  or "").strip()
            if not model_label:
                model_label = _running_local_label()
            repo_dir = (self.session.rag_settings().get("docs_dir")
                        or config.RAG_DOCS_DIR)
            if not os.path.isdir(str(repo_dir or "")):
                print("[FILES] каталог %r не найден — использую BASE_DIR"
                      % repo_dir, flush=True)
                repo_dir = config.BASE_DIR
            print("[FILES] scenario=%s root=%r model=%r apply=%s"
                  % (files_spec.get("scenario"), repo_dir, model_label,
                     files_spec.get("apply")), flush=True)
            return self._send_json(200, self._files_payload(
                files_spec, model_label, repo_dir))

        # /support: данные тикета/пользователя (MCP-сервер «Поддержка»,
        # fallback — JSON напрямую). Контекст обращения добавляется в системный
        # промпт ОТДЕЛЬНО от RAG-контекста FAQ поддержки.
        support_context_block = ""
        support_state = None
        if is_support:
            support_state = support_lookup(
                ticket_id=support_spec.get("ticket_id", ""),
                user_id=support_spec.get("user_id", ""),
                query=support_spec.get("query", ""))
            if not support_state.get("ok"):
                error = (support_state.get("error")
                         or "обращение не найдено")
                text = ("## Поддержка\n\n_Не удалось получить данные "
                        "обращения: %s_" % error)
                print("[SUPPORT] данные обращения не получены: %s" % error,
                      flush=True)
                return self._send_json(200, {
                    "ok": False, "support": True, "error": error,
                    "text": text, "html": "", "answers": [],
                    "support_source": support_state.get("source"),
                })
            ticket = support_state.get("ticket") or {}
            user = support_state.get("user") or {}
            # Вопрос для модели: текст после /support, иначе тема тикета.
            question = (support_spec.get("query")
                        or ticket.get("subject")
                        or "Помоги ответить пользователю по обращению.")
            support_context_block = support_mod.build_support_system_context(
                ticket, user)
            print("[SUPPORT] source=%s ticket=%r user=%r query=%r"
                  % (support_state.get("source"), ticket.get("id"),
                     user.get("id"), question), flush=True)

        # Глобальное ограничение max_tokens (None — не применяется)
        max_tokens = data.get("max_tokens")
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = None

        # Настройки сжатия
        compact = data.get("compact")
        if isinstance(compact, dict):
            # Клиент может прислать свои настройки (enabled/keep) — применяем.
            self.session.set_compact(compact.get("enabled"),
                                     compact.get("keep"),
                                     None)
        # Стратегия управления контекстом (если клиент прислал).
        strategy = data.get("strategy")
        if isinstance(strategy, dict):
            self.session.set_strategy(strategy.get("strategy"),
                                      strategy.get("window"))
        compact = self.session.get_compact()

        # Управление контекстом: применяем АКТИВНУЮ стратегию
        # (sliding / facts / branch) и, поверх неё, сжатие summary.
        history = self.session.get_context_messages()

        # ПРОВЕРКА НАЛИЧИЯ данных в памяти при формировании запроса:
        # если в рабочей/долговременной памяти есть данные — они уже
        # подмешаны в контекст (memory_message) как системное сообщение.
        memory = self.session.memory_state()
        mem_items = (len(memory.get("working") or {})
                     + len(memory.get("longterm") or {}))
        if mem_items:
            print("[MEMORY] в запрос добавлена память: рабочая=%d, "
                  "долговременная=%d (всего %d элементов)"
                  % (len(memory.get("working") or {}),
                     len(memory.get("longterm") or {}), mem_items),
                  flush=True)
        else:
            print("[MEMORY] память пуста — в запрос не добавляется", flush=True)

        # Профиль (персона): характер и характер ответов активного профиля
        # подставляются в системный промпт каждой модели (тон + формат/длина).
        # pid/pname/... и profile уже получены выше (см. выбор режима).

        # СОСТОЯНИЕ ЗАДАЧИ (Task State Machine): формализованный автомат
        # «этап -> шаг -> ожидаемое действие». Передаём его агенту, чтобы он
        # вёл ответ сообразно этапу и после паузы продолжал без повторных
        # объяснений. Если активной задачи нет — None.
        task_state = self.session.get_task_state()
        if not task_state.get("active"):
            task_state = None

        # ИНВАРИАНТЫ: жёсткие правила (архитектура/техрешения/стек/бизнес),
        # которые ассистент не вправе нарушать. Передаём их агенту: он учтёт
        # правила в промпте и выполнит пост-проверку ответа.
        invariants = self.session.get_invariants()
        if invariants:
            print("[INVARIANT] инвариантов в запросе: %d" % len(invariants),
                  flush=True)

        # RAG (Retrieval-Augmented Generation): при ВКЛЮЧЁННОМ RAG ищем
        # релевантные фрагменты документов и кода и подмешиваем их в промпт
        # (режим A2 — поиск всегда, модель получает готовый контекст).
        # Ошибки поиска (Ollama недоступна и т.п.) не ломают ответ модели.
        #
        # УЛУЧШЕННЫЙ RAG (2-й этап, вариант B): здесь, в СЕРВЕРЕ, выполняем
        # LLM-части (query rewrite + LLM-реранкер), т.к. RagStore намеренно не
        # зависит от LLM. RagStore делает «чистую» часть: гибридный поиск,
        # порог, пул кандидатов, а также вызывает переданные сюда callback-и.
        rag_set = self.session.rag_settings()
        rag_docs_dir = rag_set.get("docs_dir") or config.RAG_DOCS_DIR
        rag_queries = None            # формулировки запроса (rewrite)
        rag_rerank_fn = None          # callback LLM-реранкера
        help_git_note = ""            # сведения о git для /help
        if rag_set.get("enabled") and not is_help and not is_support:
            # Query rewrite: расширяем запрос синонимами/терминами через LLM.
            if rag_set.get("rewrite") and self.agent is not None:
                try:
                    extra = self.agent.rewrite_query(question)
                    if extra:
                        rag_queries = [question] + list(extra)
                        print("[RAG] query rewrite: +%d формулировок"
                              % len(extra), flush=True)
                except Exception as exc:             # noqa: BLE001
                    print("[RAG] query rewrite не выполнен: %s" % exc,
                          flush=True)
            # LLM-реранкер: пересортировка кандидатов (если включён).
            if (rag_set.get("rerank") == "llm" and self.agent is not None):
                def rag_rerank_fn(query, hits, _agent=self.agent):
                    return _agent.rag_rerank(query, hits)
        if is_help:
            # /help: ищем по документации и коду ВСЕГДА (даже если RAG
            # выключен) и добавляем сведения о git-ветке из MCP-сервера
            # «Проект». Rewrite/реранкер здесь не нужны — справка должна
            # быть быстрой и предсказуемой.
            rag_context, rag_hits = "", []
            try:
                if (rag_set.get("enabled")
                        and self.session.rag.status().get("available")):
                    rag_context, rag_hits = self.session.rag.build_context(
                        question, top_k=rag_set.get("top_k"),
                        docs_dir=rag_docs_dir)
            except Exception as exc:             # noqa: BLE001
                print("[HELP] RAG-поиск не выполнен: %s" % exc, flush=True)
            help_git_note = project_git_note(rag_docs_dir)
            if help_git_note:
                rag_context = ("СВЕДЕНИЯ О ПРОЕКТЕ (git):\n" + help_git_note
                               + ("\n\n" + rag_context if rag_context else ""))
        elif is_support:
            # /support: RAG по FAQ/документации продукта (docs/support).
            # Поиск выполняется ВСЕГДА при use_rag=True, даже если общий RAG
            # выключен, — команда должна опираться на базу знаний поддержки.
            rag_context, rag_hits = "", []
            if support_spec.get("use_rag", True) and rag_set.get("enabled"):
                rag_query = ((support_state or {}).get("rag_query")
                             or question)
                rag_context, rag_hits = support_rag_context(
                    self.session.rag, rag_query,
                    top_k=getattr(config, "SUPPORT_TOP_K", 5),
                    docs_dir=support_mod.docs_dir())
                print("[SUPPORT] RAG по FAQ: запрос=%r, фрагментов=%d"
                      % (rag_query, len(rag_hits)), flush=True)
            else:
                print("[SUPPORT] RAG по FAQ отключён (--no-rag)", flush=True)
        else:
            rag_context, rag_hits = self.session.rag_context(
                question, queries=rag_queries, rerank_fn=rag_rerank_fn)
                # НИЗКАЯ РЕЛЕВАНТНОСТЬ: RAG включён И индекс доступен (есть чанки),
        # но ни один фрагмент не прошёл порог (rag_hits пуст) — ассистент
        # ОБЯЗАН сказать «не знаю» и попросить уточнение (детерминированно,
        # без выдумывания фактов). Если индекс пуст — это не «низкая
        # релевантность», а отсутствие базы: отвечает обычный режим.
        rag_index_ok = False
        try:
            rag_index_ok = bool(self.session.rag.status().get("available"))
        except Exception:                        # noqa: BLE001
            rag_index_ok = False
        rag_low_relevance = (bool(rag_set.get("enabled")) and rag_index_ok
                             and not rag_hits and not is_help
                             and not is_support)
        rag_sources = []
        # Фрагменты для карточки ответа: текст найденного чанка + картинки
        # соответствующей страницы (если из PDF извлечены изображения).
        rag_fragments = []
        if rag_low_relevance:
            print("[RAG] релевантность ниже порога -> ответ «не знаю»",
                  flush=True)
        if rag_context:
            print("[RAG] в запрос добавлено %d фрагментов (%d символов)"
                  % (len(rag_hits), len(rag_context)), flush=True)
            # Уникальные источники (документ + страницы) для карточки ответа.
            try:
                from rtk_app import rag as rag_mod
                # Для /support источники — файлы FAQ (docs/support); сервер
                # отдаёт их через /docs/ с fallback на каталог поддержки.
                source_docs_dir = (support_mod.docs_dir() if is_support
                                   else rag_docs_dir)
                rag_sources = rag_mod.format_sources(
                    rag_hits, docs_dir=source_docs_dir)
                if rag_sources:
                    print("[RAG] источников для карточки ответа: %d"
                          % len(rag_sources), flush=True)
            except Exception as exc:                 # noqa: BLE001
                print("[RAG] не удалось собрать источники: %s" % exc,
                      flush=True)
                rag_sources = []
            # ФРАГМЕНТЫ: текст чанка + картинки его страницы.
            try:
                rag_fragments = self.session.rag_fragments(rag_hits)
                if rag_fragments:
                    print("[RAG] фрагментов с картинками: %d"
                          % len(rag_fragments), flush=True)
            except Exception as exc:                 # noqa: BLE001
                print("[RAG] не удалось собрать фрагменты: %s" % exc,
                      flush=True)
                rag_fragments = []
        else:
            print("[RAG] контекст не добавлен (выключен или индекс пуст)",
                  flush=True)

        # /help БЕЗ модели/персоны: детерминированная справка (git-ветка,
        # структура проекта и найденные фрагменты документации/кода) —
        # LLM не вызывается, ответ воспроизводим.
        if is_help and not pid and selected is None and not via_mcp:
            return self._send_json(200, self._help_payload(
                question, help_git_note, rag_hits, rag_docs_dir))

        # Календарь удалён — автоматических напоминаний нет.
        reminders = []

        # ЕСТЕСТВЕННОЕ РЕДАКТИРОВАНИЕ В ЧАТЕ: «добавь серый фон…»,
        # «измени MODEL…». Не ждём, пока MCP выберет read-only поиск:
        # читаем файл, просим модель вернуть полное новое содержимое,
        # строим diff и при явной команде применяем запись.
        natural_create = (not (is_help or is_review or is_support or is_files)
                          and _is_chat_create_request(question))
        natural_edit = (not (is_help or is_review or is_support or is_files)
                        and _is_chat_edit_request(question))
        if natural_create or natural_edit:
            edit_model = ""
            if selected:
                edit_model = str((selected[0] or {}).get("label") or "").strip()
            if not edit_model:
                edit_model = (_running_local_label() or _ServerState.mcp_model
                              or "")
            if natural_create:
                target_path = _chat_create_path(question)
                print("[FILES] естественное создание: path=%r model=%r"
                      % (target_path, edit_model), flush=True)
                result = self._files_payload({
                    "scenario": "new", "path": target_path,
                    "topic": question, "kind": "file",
                    "apply": _chat_edit_apply(question),
                }, edit_model, rag_docs_dir)
            else:
                print("[FILES] естественное редактирование: model=%r apply=%s"
                      % (edit_model, _chat_edit_apply(question)), flush=True)
                result = self._files_payload({
                    "scenario": "edit", "path": "", "instruction": question,
                    "apply": _chat_edit_apply(question),
                }, edit_model, rag_docs_dir)
        # ВЕТКА MCP: запрос идёт через MCP, если включён чекбокс.
        # Модель выбирает инструмент, инструмент вызывается на MCP-сервере,
        # его результат возвращается как ответ.
        elif via_mcp:
            selected_server = _ServerState.mcp_server or _mcp_auto_id()
            auto_mode = _mcp_is_auto(selected_server)
            print("[MCP] запрос идёт через MCP (server=%r, auto=%s, model=%r)"
                  % (selected_server, auto_mode,
                     _ServerState.mcp_model or "auto"), flush=True)
            # Модель для MCP-запроса — из селекта «модель MCP».
            # git-инструментам «Проекта» подставляем ЦЕЛЕВУЮ папку RAG
            # (docs_dir), иначе git_status/git_diff/list_files смотрели бы
            # репозиторий ассистента (1310), а не проиндексированный проект.
            tool_defaults = None
            if os.path.isdir(str(rag_docs_dir or "")):
                # git-инструментам «Проекта» — repo, файловым «Файлов» — root.
                tool_defaults = {"repo": str(rag_docs_dir),
                                 "root": str(rag_docs_dir)}
            if auto_mode:
                # АВТО: отдаём модели инструменты ВСЕХ серверов, она выбирает
                # сервер по смыслу запроса (git -> project, файл -> files,
                # тикет -> support).
                server_tools = []
                if mcp_client is not None:
                    try:
                        server_list = mcp_client.mcp_servers()
                    except Exception as exc:
                        server_list = []
                        print("[MCP] список серверов недоступен: %s" % exc,
                              flush=True)
                    for srv in server_list:
                        sid = str(srv.get("id"))
                        listed = mcp_client.mcp_list_tools(server_id=sid)
                        if listed.get("ok"):
                            server_tools.append({
                                "id": sid,
                                "label": srv.get("label") or sid,
                                "tools": listed.get("tools") or [],
                            })
                        else:
                            print("[MCP] %s: список инструментов недоступен: %s"
                                  % (sid, listed.get("error")), flush=True)
                result = self.agent.answer_via_mcp_auto(
                    question, server_tools,
                    model=(_ServerState.mcp_model or None),
                    tool_defaults=tool_defaults, history=history)
            else:
                tools = []
                if mcp_client is not None:
                    listed = mcp_client.mcp_list_tools(
                        server_id=(selected_server or None))
                    if listed.get("ok"):
                        tools = listed.get("tools", [])
                    else:
                        print("[MCP] не удалось получить список инструментов: %s"
                              % listed.get("error"), flush=True)
                result = self.agent.answer_via_mcp(
                    question, tools,
                    model=(_ServerState.mcp_model or None),
                    server_id=(selected_server or None),
                    tool_defaults=tool_defaults, history=history)
        else:
            answer_kwargs = {
                "max_tokens": max_tokens,
                "compact": compact,
                "memory": memory,
                "profile": profile,
                "answer_title": answer_title,
                "task_state": task_state,
                "invariants": invariants,
                "rag_context": (rag_context or None),
                "rag_sources": (rag_sources or None),
                "rag_fragments": (rag_fragments or None),
                "rag_low_relevance": rag_low_relevance,
            }
            if support_context_block:
                answer_kwargs["support_context"] = support_context_block
            result = self.agent.answer(question, history, selected,
                                       **answer_kwargs)
        if is_support and isinstance(result, dict):
            # Признак для UI: ответ дан в режиме поддержки; рядом — источник
            # данных обращения (MCP/JSON) и использованные FAQ-фрагменты.
            result["support"] = True
            result["support_source"] = (support_state or {}).get("source")
            result["ticket_id"] = (((support_state or {}).get("ticket")
                                    or {}).get("id"))
            result["user_id"] = (((support_state or {}).get("user")
                                  or {}).get("id"))
            result["rag_used"] = bool(rag_context)
        if result.get("ok"):
            # По одному ходу на ответ модели с уже готовой разметкой
            self.session.append_turn(question, {
                "role": "assistant",
                "content": result.get("text", ""),
                "html": result.get("html", ""),
                "answers": result.get("answers", []),
                "meta": result.get("meta", ""),
                "usage": result.get("usage"),
            })
            # Стратегия Facts: обновляем блок facts после каждого сообщения
            # пользователя (через GigaChat).
            self._maybe_update_facts(question, result)
            # Счётчики использования памяти: сколько фрагментов ответа
            # заимствовано из рабочей/долговременной памяти (для интерфейса).
            mused = result.get("memory_used") or {}
            self.session.add_memory_usage(mused.get("working", 0),
                                          mused.get("longterm", 0))
            # Сжатие: если появились вытесненные сообщения — дописываем их
            # в summary (инкрементально; работает только при keep > 0).
            self._maybe_auto_compact()
            # СОСТОЯНИЕ ЗАДАЧИ: если задача активна (не на паузе и не done) —
            # пусть агент предложит следующий переход автомата, а сервер
            # применит его ТОЛЬКО если переход корректен (проверка в
            # SessionStore.advance_task). Паузу и завершение клиент задаёт сам.
            self._maybe_advance_task(question, result)
            # Единый снимок состояния сессии (те же поля, что и /api/session).
            result.update(self.session.full_state())
        # Доставляем наступившие напоминания в чат (красным фоном) в ЛЮБОМ
        # случае — даже если ответ модели не удался, напоминание важно.
        if reminders:
            result["reminders"] = reminders
            # В историю сессии НЕ пишем: это разовая доставка, а не ход
            # диалога (иначе напоминания дублировались бы при перезагрузке).
        return self._send_json(200, result)

    def _maybe_advance_task(self, question, result):
        """Продвигает автомат задачи по ходу пользователя (если задача активна).

        Решение о переходе принимает агент (LLM на GigaChat), а сервер
        применяет его только при КОРРЕКТНОСТИ перехода. Задача на паузе не
        двигается — продолжение инициирует пользователь кнопкой «Продолжить».
        """
        try:
            ts = self.session.get_task_state()
            if not ts.get("active") or ts.get("paused") or ts.get("stage") == "done":
                return
            move = self.agent.advance_task(
                ts, question, result.get("text", ""))
            if not move or not move.get("stage"):
                return
            res = self.session.advance_task(
                stage=move.get("stage"),
                step=move.get("step"),
                expected=move.get("expected"),
                note=move.get("note") or "авто-переход по ходу диалога")
            if res.get("ok"):
                print("[TASK] этап -> %s (шаг: %s)"
                      % (res["task"].get("stage"), res["task"].get("step")),
                      flush=True)
            else:
                print("[TASK] переход отклонён: %s" % res.get("error"),
                      flush=True)
        except Exception as exc:
            print("[TASK] авто-переход не удался: %s" % exc, flush=True)

    def _maybe_update_facts(self, question, result):
        """Обновляет блок facts после хода пользователя (стратегия Facts)."""
        try:
            if self.session.get_strategy().get("strategy") != "facts":
                return
            prev = self.session.get_facts()
            last_turn = [
                {"role": "user", "content": str(question)},
                {"role": "assistant", "content": result.get("text", "")},
            ]
            facts = self.agent.update_facts(prev, last_turn)
            if isinstance(facts, dict):
                self.session.set_facts(facts)
        except Exception as exc:
            print("[FACTS] обновление не удалось: %s" % exc, flush=True)

    def _handle_compact(self):
        """Обновляет настройки сжатия сессии (enabled/keep/summary)."""
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        enabled = data.get("enabled")
        keep = data.get("keep")
        summary = data.get("summary")
        # Пустой summary из клиента НЕ должен затирать уже сгенерированный
        # на сервере — иначе настройки каждый раз обнуляют сжатие.
        if not summary:
            summary = None
        self.session.set_compact(enabled, keep, summary)
        return self._send_json(200, {
            "ok": True,
            "compact": self.session.get_compact(),
        })

    def _handle_compact_summary(self):
        """Дописывает вытесненную часть истории в summary (по запросу).

        Сжимаем только то, что вышло за пределы последних keep сообщений,
        и ДОПИСЫВАЕМ это в существующее summary (инкрементально, Вариант A).
        Summary сохраняется отдельно и будет подставлено в следующий запрос
        вместо вытесненной части истории.
        """
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        # Ключи облачных моделей из интерфейса — только на этот запрос.
        self._apply_session_keys(data)
        keep = data.get("keep", config.COMPACT_KEEP)
        try:
            keep = max(0, int(keep))
        except (TypeError, ValueError):
            keep = config.COMPACT_KEEP
        # Применяем актуальный keep перед вычислением вытесняемой части.
        self.session.set_compact(True, keep, None)
        head, end = self.session.head_to_compact()
        if not head:
            return self._send_json(200, {
                "ok": True, "summary": self.session.get_compact().get("summary", ""),
                "detail": "Нет вытесненных сообщений — сжимать нечего.",
            })
        prev_summary = self.session.get_compact().get("summary", "")
        summary = self.agent.compact_update(prev_summary, head)
        if summary:
            self.session.apply_summary(summary, upto=end, keep=keep)
            return self._send_json(200, {
                "ok": True, "summary": summary,
                "detail": "Summary дополнен (%d сообщ.)." % len(head),
            })
        return self._send_json(200, {
            "ok": False, "summary": self.session.get_compact().get("summary", ""),
            "detail": "Не удалось обновить summary.",
        })

    def _handle_strategy(self):
        """Управление стратегией контекста: none / sliding / facts / branch."""
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        state = self.session.set_strategy(data.get("strategy"),
                                          data.get("window"))
        return self._send_json(200, {
            "ok": True,
            "strategy": state,
            "context": self.session.context_stats(),
            "facts": self.session.get_facts(),
            "branches": self.session.branches_state(),
        })

    def _handle_facts(self):
        """Просмотр/редактирование фактов (key-value) стратегии Facts.

        Факты — это данные ПАМЯТИ агента: каждый факт раскладывается в
        ВЫБРАННЫЙ пользователем слой памяти: mem_map = {ключ: "working"|
        "longterm"}. Ключи без явного выбора считаются рабочей памятью.
        Отдельного хранилища facts нет — читать/писать их можно через память.
        """
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        facts = data.get("facts")
        if isinstance(facts, dict):
            facts = {str(k): str(v) for k, v in facts.items()}
            mem_map = data.get("mem_map")
            if not isinstance(mem_map, dict):
                mem_map = {}
            # Раскладываем факты по слоям памяти согласно выбору у значения.
            working, longterm = {}, {}
            for k, v in facts.items():
                layer = str(mem_map.get(k, "working")).strip().lower()
                if layer == "longterm":
                    longterm[k] = v
                else:
                    working[k] = v
            try:
                self.session.set_memory_bulk("working", working)
                self.session.set_memory_bulk("longterm", longterm)
            except ValueError as exc:
                return self._send_json(400, {"ok": False, "error": str(exc)})
        return self._send_json(200, {
            "ok": True,
            "facts": self.session.get_facts(),
            "memory": self.session.memory_state(),
        })

    def _handle_memory(self):
        """Память агента: чтение/запись трёх типов (short/working/longterm).

        Тело запроса (все поля необязательны, но action определяет смысл):
            action: "state"  — вернуть снимок всех типов (по умолчанию);
                    "set"    — записать одну пару: {type, key, value};
                    "delete" — удалить ключ: {type, key};
                    "replace"— заменить тип целиком: {type, data:{…}};
                    "clear"  — очистить тип: {type}.
            type:   "working" | "longterm" (для "short" запись запрещена —
                    краткосрочная память ведётся самим диалогом).

        Именно здесь реализован ЯВНЫЙ выбор «что и куда сохраняется» (задание
        B2): тип памяти задаётся вызывающим кодом, слои не пересекаются.
        """
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        action = str(data.get("action", "state")).strip().lower()
        mem_type = data.get("type")

        try:
            if action == "set":
                entry = self.session.set_memory_key(
                    mem_type, data.get("key"), data.get("value"))
                return self._send_json(200, {
                    "ok": True, "saved": entry,
                    "memory": self.session.memory_state(),
                })
            if action == "delete":
                removed = self.session.delete_memory_key(mem_type,
                                                         data.get("key"))
                return self._send_json(200, {
                    "ok": True, "removed": removed,
                    "memory": self.session.memory_state(),
                })
            if action == "replace":
                saved = self.session.set_memory_bulk(mem_type, data.get("data"))
                return self._send_json(200, {
                    "ok": True, "saved": saved,
                    "memory": self.session.memory_state(),
                })
            if action == "clear":
                mt = str(mem_type or "").strip()
                if mt not in config.MEMORY_TYPES:
                    return self._send_json(400, {
                        "ok": False,
                        "error": "Неизвестный тип памяти: %r" % (mem_type,),
                    })
                if mt == "short":
                    return self._send_json(400, {
                        "ok": False,
                        "error": "Краткосрочная память (диалог) очищается "
                                 "кнопкой «Новый разговор».",
                    })
                self.session.set_memory_bulk(mt, {})
                return self._send_json(200, {
                    "ok": True, "memory": self.session.memory_state(),
                })
            # action == "state" и всё прочее — отдаём снимок.
            return self._send_json(200, {
                "ok": True, "memory": self.session.memory_state(),
            })
        except ValueError as exc:
            return self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send_json(500, {"ok": False,
                                         "error": "Ошибка памяти: %s" % exc})

    def _handle_branches(self):
        """Управление ветками: create / switch / delete / rename.

        Ожидаемые поля: action = "create" | "switch" | "delete" | "rename",
        name (для create/rename), count (для create),
        index (для switch/delete/rename).
        """
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        action = str(data.get("action", "")).strip().lower()
        if action == "create":
            state = self.session.create_branch(
                name=data.get("name"),
                count=data.get("count"),
                from_checkpoint=bool(data.get("from_checkpoint", True)))
        elif action == "switch":
            state = self.session.switch_branch(data.get("index"))
        elif action == "delete":
            state = self.session.delete_branch(data.get("index"))
        elif action == "rename":
            state = self.session.rename_branch(data.get("index"),
                                               data.get("name"))
        else:
            return self._send_json(400, {
                "ok": False,
                "error": "Неизвестное действие с ветками (action).",
            })
        return self._send_json(200, {
            "ok": True,
            "branches": state,
            "messages": self.session.snapshot(),
        })

    def _handle_task(self):
        """Управление СОСТОЯНИЕМ ЗАДАЧИ (Task State Machine).

        Задача — конечный автомат: этап (planning -> execution -> validation
        -> done) + текущий шаг + ожидаемое действие. Поддерживает ПАУЗУ на
        любом этапе и ПРОДОЛЖЕНИЕ без повторных объяснений (цель и журнал
        сохраняются).

        Ожидаемые поля: action =
            "start"   -> {goal, step?, expected?}   завести новую задачу;
            "advance" -> {stage?, step?, expected?, note?}  корректный переход;
            "pause"   -> {note?}                     поставить на паузу;
            "resume"  -> {note?}                     снять с паузы (продолжить);
            "finish"  -> {note?}                     завершить (из validation);
            "reset"                                  сбросить состояние;
            "state"                                  вернуть снимок (по умолч.).
        """
        data = self._read_json_body()
        if not data:
            # GET-подобный вызов (без тела) — просто отдаём состояние.
            return self._send_json(200, {
                "ok": True, "task": self.session.get_task_state()})
        action = str(data.get("action", "state")).strip().lower()
        try:
            if action == "start":
                task = self.session.start_task(
                    data.get("goal"), step=data.get("step", ""),
                    expected=data.get("expected", ""),
                    stage=data.get("stage", "planning"))
                return self._send_json(200, {"ok": True, "task": task})
            if action == "advance":
                res = self.session.advance_task(
                    stage=data.get("stage"), step=data.get("step"),
                    expected=data.get("expected"), note=data.get("note", ""))
                status = 200 if res.get("ok") else 409
                return self._send_json(status, {
                    "ok": res.get("ok"), "task": res.get("task"),
                    "error": res.get("error")})
            if action == "pause":
                res = self.session.pause_task(data.get("note", ""))
                return self._send_json(200, {"ok": res.get("ok"),
                                             "task": res.get("task")})
            if action == "resume":
                res = self.session.resume_task(data.get("note", ""))
                return self._send_json(200, {"ok": res.get("ok"),
                                             "task": res.get("task")})
            if action == "finish":
                res = self.session.finish_task(data.get("note", ""))
                status = 200 if res.get("ok") else 409
                return self._send_json(status, {
                    "ok": res.get("ok"), "task": res.get("task"),
                    "error": res.get("error")})
            if action == "reset":
                task = self.session.reset_task()
                return self._send_json(200, {"ok": True, "task": task})
            # action == "state" и всё прочее.
            return self._send_json(200, {
                "ok": True, "task": self.session.get_task_state()})
        except ValueError as exc:
            return self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send_json(500, {
                "ok": False, "error": "Ошибка состояния задачи: %s" % exc})

    def _handle_profiles(self):
        """Управление ПРОФИЛЯМИ (персонами).

        Профиль — именованная персона со СВОЕЙ памятью (рабочая +
        долговременная), своим диалогом и настройками. Атрибуты character
        (характер — тон общения) и style (характер ответов — формат/длина)
        задаются ПРИ СОЗДАНИИ и подставляются в системный промпт каждой
        модели. Переключение профиля заменяет активную память и диалог.

        Ожидаемые поля: action = "create" | "switch" | "update" | "delete";
        id (для switch/update/delete); name, character, style, model (для
        create/update). model — метка модели, которой отвечает персона.
        """
        data = self._read_json_body()
        if not data:
            return self._send_json(400, {"ok": False, "error": "Bad JSON."})
        action = str(data.get("action", "list")).strip().lower()
        try:
            if action == "create":
                state = self.session.create_profile(
                    data.get("name"),
                    character=data.get("character"),
                    style=data.get("style"),
                    model=data.get("model"),
                    activate=bool(data.get("activate", True)))
            elif action == "switch":
                state = self.session.switch_profile(data.get("id"))
            elif action == "update":
                state = self.session.update_profile(
                    data.get("id"),
                    name=data.get("name"),
                    character=data.get("character"),
                    style=data.get("style"),
                    model=data.get("model"))
            elif action == "delete":
                state = self.session.delete_profile(data.get("id"))
            elif action in ("list", "state", ""):
                state = self.session.profiles_state()
            else:
                return self._send_json(400, {
                    "ok": False,
                    "error": "Неизвестное действие с профилями (action).",
                })
        except ValueError as exc:
            return self._send_json(400, {"ok": False, "error": str(exc)})
        # После переключения/удаления активным может стать другой профиль —
        # возвращаем актуальные память и диалог, чтобы интерфейс обновился.
        return self._send_json(200, {
            "ok": True,
            "profiles": state.get("profiles", []),
            "active": state.get("active"),
            "messages": self.session.snapshot(),
            "memory": self.session.memory_state(),
            "branches": self.session.branches_state(),
            "compact": self.session.get_compact(),
            "strategy": self.session.get_strategy(),
            "context": self.session.context_stats(),
        })

    def _handle_invariants(self):
        """Управление ИНВАРИАНТАМИ (правилами, которые нельзя нарушать).

        Инварианты хранятся ОТДЕЛЬНО от диалога (свой раздел профиля) и имеют
        КАТЕГОРИЮ (архитектура / техрешения / стек / бизнес-правила). При
        конфликте запроса с инвариантом ассистент отказывается от решения.

        Ожидаемые поля: action =
            "add"     -> {text, category}            добавить инвариант;
            "update"  -> {id, text?, category?}      изменить инвариант;
            "delete"  -> {id}                         удалить инвариант;
            "replace" -> {invariants:[{text,category,id?}, …]}  заменить весь список;
            "clear"                                   очистить список;
            "state"                                   вернуть снимок (по умолч.).
        """
        data = self._read_json_body() or {}
        action = str(data.get("action", "state")).strip().lower()
        try:
            if action == "add":
                state = self.session.add_invariant(
                    data.get("text"), data.get("category", "business"))
            elif action == "update":
                state = self.session.update_invariant(
                    data.get("id"), text=data.get("text"),
                    category=data.get("category"))
            elif action == "delete":
                removed = self.session.delete_invariant(data.get("id"))
                state = self.session.invariants_state()
                state["removed"] = removed
            elif action == "replace":
                state = self.session.set_invariants(data.get("invariants"))
            elif action == "clear":
                state = self.session.clear_invariants()
            else:  # "state" и всё прочее
                state = self.session.invariants_state()
        except ValueError as exc:
            return self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send_json(500, {
                "ok": False, "error": "Ошибка инвариантов: %s" % exc})
        return self._send_json(200, state)

    def _handle_mcp(self):
        """Управление MCP: чтение/запись настроек и проверка статуса сервера.

        Тело запроса (все поля необязательны, action определяет смысл):
            action: "state"  — вернуть состояние MCP (по умолчанию);
                    "set"    — сохранить настройки {enabled?, model?};
                    "status" — ПРОВЕРИТЬ статус MCP-сервера: подключиться,
                               получить список инструментов и вернуть
                               результат (для кнопки «Проверить статус»).
            enabled — bool: включать ли использование инструментов MCP;
            model   — метка модели, применяемой при работе с MCP.
        """
        data = self._read_json_body() or {}
        action = str(data.get("action", "state")).strip().lower()

        if action == "set":
            with _ServerState.mcp_lock:
                if "enabled" in data:
                    _ServerState.composition_running = False
                    _ServerState.mcp_enabled = bool(data.get("enabled"))
                if "model" in data:
                    _ServerState.mcp_model = str(data.get("model") or "")
                if "server" in data:
                    requested = str(data.get("server") or "")
                    valid = _mcp_valid_server_ids()
                    _ServerState.mcp_server = (requested if requested in valid
                                               else getattr(
                                                   config,
                                                   "MCP_SERVER_DEFAULT",
                                                   _mcp_auto_id()))
                _save_mcp_settings()
            return self._send_json(200, _mcp_state_payload(check=False))

        if action == "status":
            return self._send_json(200, _mcp_state_payload(check=True))

        if action in ("compose", "compose_stop"):
            return self._send_json(200, {
                "ok": False,
                "error": "Композиция MCP удалена. Доступны серверы project, "
                         "support, files и режим auto.",
            })

        # action == "state" и всё прочее — отдаём состояние без проверки.
        return self._send_json(200, _mcp_state_payload(check=False))

    def _handle_rag(self):
        """Управление RAG: настройки, индексация, очистка, проверка модели.

        Тело запроса (все поля необязательны, action определяет смысл):
            action: "state"   — вернуть состояние RAG (по умолчанию);
                    "set"     — сохранить настройки {enabled?, docs_dir?,
                                top_k?};
                    "reindex" — ПЕРЕИНДЕКСИРОВАТЬ папку PDF: извлечь текст,
                                нарезать чанки, посчитать эмбеддинги и
                                записать в SQLite-индекс; вернуть отчёт;
                    "clear"   — очистить RAG-индекс (чанки + метаданные);
                    "check"   — проверить доступность модели эмбеддингов
                                Ollama (nomic-embed-text-v2-moe).
        """
        data = self._read_json_body() or {}
        action = str(data.get("action", "state")).strip().lower()

        if action == "set":
            status = self.session.set_rag_settings(
                enabled=(data.get("enabled")
                         if "enabled" in data else None),
                docs_dir=(data.get("docs_dir")
                          if "docs_dir" in data else None),
                top_k=(data.get("top_k") if "top_k" in data else None),
                rewrite=(data.get("rewrite")
                         if "rewrite" in data else None),
                hybrid=(data.get("hybrid")
                        if "hybrid" in data else None),
                hybrid_alpha=(data.get("hybrid_alpha")
                              if "hybrid_alpha" in data else None),
                pool_mult=(data.get("pool_mult")
                           if "pool_mult" in data else None),
                min_score=(data.get("min_score")
                           if "min_score" in data else None),
                rerank=(data.get("rerank") if "rerank" in data else None))
            return self._send_json(200, status)

        if action == "reindex":
            if not self.session.rag_settings().get("enabled"):
                return self._send_json(409, {
                    "ok": False,
                    "error": "RAG выключен — индексация и Ollama не "
                             "используются"})
            # Путь для индексации: из запроса или из сохранённых настроек.
            docs_dir = data.get("docs_dir")
            if docs_dir is not None:
                self.session.set_rag_settings(docs_dir=str(docs_dir))
            print("[RAG] запущена (пере)индексация папки %r"
                  % (docs_dir or self.session.rag_settings().get("docs_dir")),
                  flush=True)
            report = self.session.rag_reindex(docs_dir=docs_dir)
            payload = self.session.rag_status()
            payload["report"] = report
            return self._send_json(200, payload)

        if action == "clear":
            status = self.session.rag_clear()
            print("[RAG] индекс очищен.", flush=True)
            return self._send_json(200, status)

        if action == "check":
            payload = self.session.rag_status()
            if not payload.get("enabled"):
                payload["embed_check"] = {
                    "ok": False,
                    "error": "RAG выключен — Ollama не используется"}
                return self._send_json(200, payload)
            from rtk_app import rag as rag_mod
            payload["embed_check"] = rag_mod.embed_available()
            return self._send_json(200, payload)

        # action == "state" и всё прочее — отдаём состояние.
        return self._send_json(200, self.session.rag_status())

    def _handle_embed(self):
        """Управление моделью эмбеддингов RAG (Ollama) — reuse-first.

        Действия:
            "state"/"status" — снимок блока (скан, runtime, active, reuse);
            "scan"     — скан корня на VPS (Ollama установлена локально);
            "pc_scan"  — скан корня на ПК через служебный API агента;
            "start"    — запуск/переиспользование Ollama на VPS;
            "pc_start" — запуск/переиспользование Ollama на ПК;
            "stop"/"pc_stop" — остановка (только своего процесса);
            "check"    — тестовый эмбеддинг выбранной модели (dim);
            "set"      — сохранить root/exe/models_dir/model/source/reuse.
        """
        data = self._read_json_body() or {}
        action = str(data.get("action", "state")).strip().lower()
        with _ServerState.embed_lock:
            if action in ("scan", "pc_scan"):
                root = str(data.get("root") or "").strip()
                settings = _embed_manager().load_settings()
                if not root:
                    root = str(settings.get("root") or "").strip()
                if not root:
                    root = (config.EMBED_PC_ROOT
                            if settings.get("source") == "pc"
                            else config.EMBED_LOCAL_ROOT)
                # Путь вида e:/Models на VPS не существует -> скан через
                # ПК-агент. На самом ПК (локальный запуск) он существует ->
                # скан локально. Явный pc_scan всегда идёт через агент.
                remote = (action == "pc_scan"
                          or (_is_pc_path(root)
                              and not os.path.isdir(root)))
                if remote:
                    res = _embed_scan_pc(root)
                    payload = _embed_state_payload()
                    payload["pc_result"] = res
                    return self._send_json(200, payload)
                res = _embed_scan_local(root)
                payload = _embed_state_payload()
                payload["scan"] = res
                return self._send_json(200, payload)

            if action in ("start", "pc_start"):
                return self._embed_start(data, action == "pc_start")

            if action in ("stop", "pc_stop"):
                return self._embed_stop(action == "pc_stop")

            if action == "check":
                cfg = ollama_server.active_embed_config()
                model = str(data.get("model") or cfg.get("model") or "")
                base = _embed_base_url(cfg.get("url"))
                payload = _embed_state_payload()
                if not base:
                    payload["check_result"] = {
                        "ok": False, "model": model, "dim": 0,
                        "error": "эмбеддинги не запущены"}
                    return self._send_json(200, payload)
                payload["check_result"] = ollama_server.probe_model(
                    model, url=base,
                    timeout=int(getattr(config, "RAG_EMBED_TIMEOUT", 60)))
                return self._send_json(200, payload)

            if action == "status":
                payload = _embed_state_payload()
                source = str(data.get("source")
                             or payload.get("source") or "vps").lower()
                if source == "pc":
                    payload["pc_status"] = _agent_api("/embed/status")
                if data.get("model"):
                    cfg = ollama_server.active_embed_config()
                    base = _embed_base_url(cfg.get("url"))
                    if base:
                        payload["check_result"] = ollama_server.probe_model(
                            str(data.get("model")), url=base)
                return self._send_json(200, payload)

            if action == "set":
                mgr = _embed_manager()
                settings = mgr.load_settings()
                for key in ("root", "exe", "models_dir", "model", "source",
                            "reuse"):
                    if key in data:
                        settings[key] = data.get(key)
                mgr.save_settings(settings)
                return self._send_json(200, _embed_state_payload())

            # action == "state" и всё прочее.
            return self._send_json(200, _embed_state_payload())

    def _embed_start(self, data, pc=False):
        """Запуск эмбеддингов на ПК/VPS с переиспользованием запущенной Ollama."""
        mgr = _embed_manager()
        settings = mgr.load_settings()
        root = str(data.get("root") or settings.get("root")
                   or "").strip()
        if not root:
            root = (config.EMBED_PC_ROOT
                    if (pc or settings.get("source") == "pc")
                    else config.EMBED_LOCAL_ROOT)
        pc = pc or (str(settings.get("source") or "") == "pc"
                    and not os.path.isdir(root))
        source = "pc" if pc else "vps"
        model = str(data.get("model") or settings.get("model") or "").strip()
        reuse = bool(data.get("reuse", True))
        exe = str(data.get("exe") or settings.get("exe") or "").strip()
        models_dir = str(data.get("models_dir")
                         or settings.get("models_dir") or "").strip()

        # Запуск — только по модели, выбранной ПОСЛЕ сканирования.
        scan = (_ServerState.pc_embed_scan if source == "pc"
                else _ServerState.embed_scan) or {}
        scan_models = [m for m in (scan.get("models") or [])
                       if isinstance(m, dict)]
        names = [str(m.get("name") or "") for m in scan_models
                 if m.get("name")]
        if not model:
            return self._send_json(400, {
                "ok": False, "scan_required": True,
                "error": "Сначала просканируйте папку и выберите модель "
                         "эмбеддингов."})
        if not names:
            return self._send_json(400, {
                "ok": False, "scan_required": True,
                "error": "Нет результатов сканирования — нажмите "
                         "«Сканировать» и выберите модель."})
        if not any(ollama_server.same_model(model, n) for n in names):
            return self._send_json(400, {
                "ok": False, "scan_required": True,
                "error": "Выбранная модель отсутствует в последнем скане — "
                         "обновите скан."})

        if source == "pc":
            res = _agent_api("/embed/start",
                             {"root": root, "exe": exe,
                              "models_dir": models_dir, "model": model,
                              "reuse": reuse},
                             method="POST", timeout=180)
            payload = _embed_state_payload()
            payload["pc_start_result"] = res
            if not res.get("ok"):
                return self._send_json(200 if res.get("needs_choice")
                                       else 400, payload)
            chosen = str(res.get("model") or model or "").strip()
            probe = _wait_tunnel_embed(chosen or None, timeout=60)
            if not probe.get("alive"):
                payload["ok"] = False
                payload["error"] = (
                    "Ollama на ПК запущена, но туннель %d недоступен — "
                    "перезапустите ПК-агент с --ollama-port %d"
                    % (config.EMBED_TUNNEL_PORT, config.OLLAMA_PORT))
                return self._send_json(200, payload)
            dim = res.get("dim")
            if not dim:
                for m in probe.get("models") or []:
                    if ollama_server.same_model(m.get("name"), chosen):
                        dim = m.get("embedding_length")
                        break
            _record_embed_selection({"model": chosen, "dim": dim}, "pc",
                                    root=root, exe=exe,
                                    models_dir=models_dir)
            payload = _embed_state_payload()
            payload["pc_start_result"] = res
            return self._send_json(200, payload)

        # VPS: локальный запуск/переиспользование.
        if not exe or not models_dir:
            scan = mgr.scan(root)
            exe = exe or (scan.get("servers") or [{}])[0].get("path") or ""
            models_dir = models_dir or scan.get("models_dir") or ""
        settings.update({"root": root, "exe": exe,
                         "models_dir": models_dir, "model": model,
                         "source": "vps", "reuse": reuse})
        mgr.save_settings(settings)
        res = mgr.start(exe=exe, models_dir=models_dir, model=model,
                        reuse=reuse)
        payload = _embed_state_payload()
        payload["start_result"] = res
        if res.get("ok"):
            _record_embed_selection(res, "vps", root=root, exe=exe,
                                    models_dir=models_dir)
            payload = _embed_state_payload()
            payload["start_result"] = res
            return self._send_json(200, payload)
        return self._send_json(200 if res.get("needs_choice") else 400,
                               payload)

    def _embed_stop(self, pc=False):
        """Остановка своего процесса Ollama; внешнюю не трогаем."""
        settings = ollama_server.load_settings()
        if not pc:
            # settings.source=pc, но путь существует локально -> это локальный
            # запуск (разработка на ПК), останавливаем свой процесс.
            pc = (str(settings.get("source") or "") == "pc"
                  and not os.path.isdir(settings.get("root") or ""))
        if pc:
            res = _agent_api("/embed/stop", {}, method="POST", timeout=60)
        else:
            res = _embed_manager().stop()
        if not res.get("external"):
            settings["enabled"] = False
            settings["managed"] = True
            settings["backend"] = "none"
            settings["url"] = ""
            ollama_server.save_settings(settings)
        payload = _embed_state_payload()
        payload["stop_result"] = res
        payload["source"] = "pc" if pc else "vps"
        return self._send_json(200, payload)

    def _handle_local(self):
        """Управление ЛОКАЛЬНОЙ моделью (llama.cpp / Ollama / LM Studio).

        Тело запроса (action определяет смысл):
            "state"  — вернуть состояние блока (по умолчанию);
            "scan"   — ПРОСКАНИРОВАТЬ папку {root}: найти сервер llama.cpp и
                       *.gguf-модели, вернуть список + предложенные параметры;
            "params" — подобрать параметры запуска для модели {model}
                       (ngl — автоподбор по видеопамяти);
            "start"  — ЗАПУСТИТЬ сервер {server_path?, params?}: поднять
                       процесс, дождаться готовности, вернуть URL (…/v1);
            "stop"   — остановить сервер (вручную);
            "status" — проверить живость сервера и список его моделей;
            "models" — список моделей запущенного сервера (GET /v1/models);
            "set"    — сохранить настройки {enabled?, root?, server_path?,
                       params?}.
        """
        data = self._read_json_body() or {}
        action = str(data.get("action", "state")).strip().lower()
        mgr = _local_manager()
        with _ServerState.local_lock:
            if action == "scan":
                root = str(data.get("root") or "").strip()
                if root:
                    settings = mgr.load_settings()
                    settings["root"] = root
                    mgr.save_settings(settings)
                scan = local_server.scan_root(root)
                _ServerState.local_scan = scan
                payload = _local_status_payload()
                if scan.get("models"):
                    payload["suggest"] = local_server.suggest_params(
                        scan["models"][0]["path"])
                payload["scan"] = scan
                return self._send_json(200, payload)

            if action == "params":
                model_path = str(data.get("model") or "").strip()
                if not model_path and _ServerState.local_scan:
                    models = _ServerState.local_scan.get("models") or []
                    if models:
                        model_path = models[0]["path"]
                if not model_path or not os.path.isfile(model_path):
                    return self._send_json(
                        400, {"ok": False, "error": "модель не найдена"})
                params = local_server.suggest_params(model_path)
                return self._send_json(200, {"ok": True, "params": params})

            if action == "start":
                # Слабая модель VPS всегда работает под systemd. Повторный
                # запуск не нужен и привёл бы к конфликту порта 8080.
                runtime = mgr.status()
                if runtime.get("ready"):
                    payload = _local_status_payload()
                    payload["start"] = {"ok": True, "already": True,
                                        "url": runtime.get("url"),
                                        "pid": runtime.get("pid")}
                    return self._send_json(200, payload)
                params = dict(data.get("params") or {})
                # Запуск — только по явному выбору после сканирования.
                model = str(params.get("model") or "").strip()
                err = _model_selection_error(
                    model, _ServerState.local_scan or {})
                if err:
                    return self._send_json(400, {
                        "ok": False, "error": err,
                        "scan_required": True})
                server_path = str(data.get("server_path")
                                  or mgr.load_settings().get("server_path")
                                  or "").strip()
                if not server_path:
                    scan = _ServerState.local_scan or local_server.scan_root(
                        mgr.load_settings().get("root") or "")
                    for s in scan.get("servers", []):
                        if s.get("kind") == "llama.cpp":
                            server_path = s.get("path")
                            break
                # Автоподбор параметров, если модель задана, а ngl не задан.
                if params.get("model") and "ngl" not in params:
                    sugg = local_server.suggest_params(params["model"])
                    for k, v in sugg.items():
                        params.setdefault(k, v)
                result = mgr.start(server_path, params)
                if result.get("ok"):
                    settings = mgr.load_settings()
                    settings["enabled"] = True
                    settings["server_path"] = server_path
                    settings["params"] = params
                    mgr.save_settings(settings)
                    _sync_agent_local()
                    print("[LOCAL] сервер запущен: %s (pid=%s)"
                          % (result.get("url"), result.get("pid")), flush=True)
                else:
                    print("[LOCAL] запуск не удался: %s"
                          % result.get("error"), flush=True)
                payload = _local_status_payload()
                payload["start"] = result
                return self._send_json(200, payload)

            if action == "stop":
                stopped = mgr.stop()
                settings = mgr.load_settings()
                settings["enabled"] = False
                mgr.save_settings(settings)
                _sync_agent_local()
                print("[LOCAL] сервер остановлен (stopped=%s)." % stopped,
                      flush=True)
                payload = _local_status_payload()
                payload["stopped"] = stopped
                return self._send_json(200, payload)

            if action == "status":
                runtime = mgr.status()
                _ServerState.local_runtime = runtime
                _sync_agent_local()
                payload = _local_status_payload()
                payload["runtime"] = runtime
                return self._send_json(200, payload)

            if action == "models":
                runtime = mgr.status()
                return self._send_json(200, {"ok": True,
                                             "models": runtime.get("models")
                                             or []})

            if action == "set":
                settings = mgr.load_settings()
                if "enabled" in data:
                    settings["enabled"] = bool(data.get("enabled"))
                if "root" in data:
                    settings["root"] = str(data.get("root") or "")
                if "server_path" in data:
                    settings["server_path"] = str(data.get("server_path") or "")
                if "params" in data and isinstance(data.get("params"), dict):
                    merged = dict(settings.get("params") or {})
                    merged.update(data.get("params") or {})
                    settings["params"] = merged
                mgr.save_settings(settings)
                _sync_agent_local()
                return self._send_json(200, _local_status_payload())

            if action == "pc_scan":
                # СКАН ПАПКИ НА ПК: VPS напрямую папку ПК не видит, поэтому
                # просим ПК-агента просканировать путь и вернуть список.
                root = str(data.get("root") or "").strip()
                pc_root = str(getattr(config, "PC_ROOT", "") or "").strip()
                # Поле «корень папки» в UI на VPS содержит путь VPS
                # (/opt/models). Для скана ПК подставляем PC_ROOT.
                if pc_root and (not root or root == config.LOCAL_ROOT):
                    root = pc_root
                res = _agent_api("/scan", {"root": root}, method="POST")
                if res.get("ok") or res.get("models") is not None:
                    _ServerState.pc_scan = {
                        "models": res.get("models") or [],
                        "servers": res.get("servers") or [],
                        "root": root,
                    }
                payload = _local_status_payload()
                payload["pc_result"] = res
                models = res.get("models") or []
                if models:
                    sug = _agent_api("/params",
                                     {"model": models[0].get("path")},
                                     method="POST", timeout=30)
                    if sug.get("ok"):
                        payload["suggest"] = sug.get("params")
                return self._send_json(200, payload)

            if action == "pc_params":
                model = str(data.get("model") or "").strip()
                if not model:
                    return self._send_json(
                        400, {"ok": False, "error": "модель не указана"})
                res = _agent_api("/params", {"model": model},
                                 method="POST", timeout=30)
                return self._send_json(
                    200 if res.get("ok") else 400,
                    {"ok": bool(res.get("ok")), "params": res.get("params"),
                     "error": res.get("error")})

            if action == "pc_start":
                # Запуск модели НА ПК по команде из единого блока UI.
                params = dict(data.get("params") or {})
                server_path = str(data.get("server_path") or "").strip()
                scan = _ServerState.pc_scan or {}
                if not server_path:
                    for srv in scan.get("servers") or []:
                        if srv.get("kind") == "llama.cpp":
                            server_path = srv.get("path")
                            break
                model = str(params.get("model") or "").strip()
                err = _model_selection_error(model, scan)
                if err:
                    return self._send_json(400, {
                        "ok": False, "error": err,
                        "scan_required": True})
                if not server_path or not model:
                    return self._send_json(400, {
                        "ok": False,
                        "error": "сначала укажите путь, просканируйте папку "
                                 "ПК и выберите модель"})
                if not params.get("ngl"):
                    # 0/пусто — автоподбор на ПК (ngl по свободной VRAM).
                    sug = _agent_api("/params", {"model": model},
                                     method="POST", timeout=30)
                    if sug.get("ok"):
                        for k, v in (sug.get("params") or {}).items():
                            params[k] = v
                        params["model"] = model
                params.setdefault("host", "127.0.0.1")
                params.setdefault("port", config.LOCAL_PORT)
                res = _agent_api(
                    "/start",
                    {"server_path": server_path, "params": params},
                    method="POST", timeout=300)
                if res.get("ok") and res.get("already"):
                    # На ПК уже запущен движок — используем его, новый не
                    # поднимаем. Показываем реальную модель и предупреждаем,
                    # если она отличается от выбранной в UI.
                    res["reused"] = True
                    st = _agent_api("/status")
                    eng = (st.get("engine") or {}) if isinstance(st, dict) else {}
                    models = eng.get("models") or []
                    if models:
                        res["models"] = models
                        res["model"] = models[0]
                    requested = str(params.get("model") or "")
                    if requested and res.get("model") and requested != res["model"]:
                        res["model_mismatch"] = True
                        res["requested_model"] = requested
                    res["external"] = not bool(eng.get("pid"))
                up = {}
                if res.get("ok"):
                    # Ждём, пока движок ПК станет виден через туннель.
                    deadline = time.time() + 60
                    while time.time() < deadline:
                        if _tunnel_probe().get("alive"):
                            break
                        time.sleep(1.0)
                    up = _activate_strong()
                    if up.get("ok"):
                        _start_tunnel_tick()
                payload = _local_status_payload()
                payload["pc_start_result"] = res
                payload["tunnel_result"] = up
                return self._send_json(200 if res.get("ok") else 400, payload)

            if action == "pc_stop":
                res = _agent_api("/stop", {}, method="POST", timeout=30)
                down = _activate_weak()
                payload = _local_status_payload()
                payload["pc_stop_result"] = res
                payload["tunnel_result"] = down
                return self._send_json(200, payload)

            if action == "pair":
                # Подготовка подключения к ПК (команда запуска агента).
                payload = _local_status_payload()
                payload["pair"] = _pair_payload()
                payload["tunnel"] = _tunnel_payload()
                return self._send_json(200, payload)

            if action == "tunnel_up":
                # Включить СИЛЬНУЮ модель (ПК).
                up = _activate_strong()
                if up.get("ok"):
                    _start_tunnel_tick()
                payload = _local_status_payload()
                payload["tunnel_result"] = up
                return self._send_json(200, payload)

            if action == "tunnel_down":
                # Вернуть СЛАБУЮ модель (VPS).
                _stop_tunnel_tick()
                down = _activate_weak()
                payload = _local_status_payload()
                payload["tunnel_result"] = down
                return self._send_json(200, payload)

            if action == "confirm_status":
                res = _agent_api("/confirm_status")
                return self._send_json(200, {"ok": True, "confirm": res,
                                             "tunnel": _tunnel_payload()})

            if action == "embed_status":
                probe = _embed_probe()
                return self._send_json(200, {"ok": True, "embed": probe,
                                             "tunnel": _tunnel_payload()})

            # action == "state" и всё прочее.
            return self._send_json(200, _local_status_payload())


def create_server(agent, host=None, port=None):
    """Создаёт HTTP-сервер, связанный с конкретным экземпляром агента."""
    if agent is None:
        raise ValueError("create_server: требуется экземпляр агента (Agent).")
    _set_agent(agent)
    # ВАЖНО: проверяем именно None, а не «ложность»: port=0 — валидное
    # значение (ОС сама выберет свободный порт), которое нельзя подменять
    # значением по умолчанию, иначе тесты/инстансы будут конфликтовать
    # на общем порту config.WEB_PORT.
    addr = (host or config.WEB_HOST,
            config.WEB_PORT if port is None else port)

    class _ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    return _ReusableServer(addr, WebRequestHandler)


def _open_browser_later(url, delay=1.0):
    def _job():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as exc:
            print("[WEB] браузер: %s" % exc)
    threading.Thread(target=_job, daemon=True).start()


def serve(agent_or_key, host=None, port=None, open_page=True, agent=None):
    """Запускает сервер, используя агента в качестве единой сущности.

    agent_or_key — либо строка API-ключа DeepSeek (тогда создаётся агент),
    либо уже готовый экземпляр rtk_app.agent.Agent.
    agent — ещё один способ передать готового агента явно.
    """
    if agent is None:
        agent = agent_or_key

    # Загружаем сохранённые настройки MCP (вкл/выкл + модель).
    _load_mcp_settings()

    # Агент — отдельная сущность, построенная вокруг ключа либо переданная.
    if not isinstance(agent, Agent):
        agent = Agent(agent)

    # ВАЖНО: запоминаем агента ДО синхронизации локальной модели.
    _set_agent(agent)

    # ЛОКАЛЬНАЯ МОДЕЛЬ: если она была включена в прошлой сессии — добавляем
    # её источник в состав агента (появится в селекторе моделей).
    try:
        _sync_agent_local()
    except Exception as exc:
        print("[LOCAL] не удалось подключить локальную модель: %s" % exc,
              flush=True)

    # VPS: фоновый сборщик клиентских сессий (ACTIVE->CHECK->OFFLINE).
    try:
        session_mgr.get_manager()
        session_mgr.start_gc_thread()
    except Exception as exc:                       # noqa: BLE001
        print("[SESSION] gc-поток не запущен: %s" % exc, flush=True)

    # ЛОКАЛЬНАЯ LLM С ПК: по умолчанию активна ТОЛЬКО слабая модель VPS.
    # Сильную модель пользователь включает сам (кнопка/действие в UI);
    # при старте сервера авто-переключения на неё нет.
    try:
        if _initial_engine() == "strong":
            _activate_strong()
        else:
            _activate_weak()
        _start_tunnel_tick()
    except Exception as exc:                       # noqa: BLE001
        print("[TUNNEL] авто-инициализация не удалась: %s" % exc, flush=True)

    httpd = create_server(agent, host, port)
    shown_host, shown_port = httpd.server_address[:2]
    url = "http://%s:%s/" % (shown_host, shown_port)
    print("[WEB] Сервер запущен: %s" % url)
    print("[WEB] Агент обслуживает модели: %s" % agent.label)
    _ServerState.composition_running = False
    print("[MCP] доступны серверы: project, support, files", flush=True)
    if open_page and getattr(config, "OPEN_BROWSER", True):
        _open_browser_later(url)
    print("[WEB] Остановка: нажмите Ctrl+C.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[WEB] Остановка...")
    finally:
        _stop_tunnel_tick()
        httpd.server_close()


def main():
    import sys
    from rtk_app.key_store import read_api_key
    try:
        api_key = read_api_key(config.DS_KEY_FILE)
        print("[OK] Ключ DeepSeek прочитан из %s" % config.DS_KEY_FILE)
    except FileNotFoundError:
        # Файла ключа нет: сервер всё равно стартует — интерфейс откроется,
        # а недоступные модели покажут подсказку, как добавить ключ
        # (самодиагностика уже напечатана через rtk_web.py/key_check).
        api_key = ""
        print("[!] Файл %s не найден. DeepSeek-flash в чате станет "
              "недоступен до тех пор, пока вы не впишете ключ в apidpsk.txt."
              % config.DS_KEY_FILE)
    args = sys.argv[1:]
    host, port = config.WEB_HOST, config.WEB_PORT
    if args and args[0].isdigit():
        port = int(args[0])
        if len(args) > 1:
            host = args[1]
    sys.exit(serve(api_key, host, port) or 0)