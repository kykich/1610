# -*- coding: utf-8 -*-
"""Модель эмбеддингов RAG (Ollama): поиск, выбор, запуск, переиспользование.

Блок решает те же задачи, что ``local_server`` для чат-модели:

1. СКАНИРОВАНИЕ корня (напр. ``e:\\Models``): находит ``ollama.exe`` и
   каталог моделей Ollama (``manifests`` + ``blobs``). Модели читаются из
   манифестов, поэтому список доступен ещё ДО запуска сервера.
2. ВЫБОР модели: embedding-модели помечаются по ``capabilities`` (если
   сервер уже работает) или по имени (``embed``/``bge``/``e5``/``nomic``…).
3. ЗАПУСК: ``ollama serve`` с ``OLLAMA_MODELS`` найденного хранилища.
4. REUSE-FIRST: если Ollama уже запущена (трей ``ollama app.exe`` или
   вручную), используется ОНА; второй процесс не поднимается — экономия
   памяти. Если выбранной модели в ней нет, возвращается ``needs_choice``
   со списком доступных моделей.
5. ОСТАНОВКА только своего процесса. Внешнюю Ollama не трогаем: она может
   обслуживать другие приложения.

Модуль кроссплатформенный (Windows / Linux), зависимостей нет.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import config

__all__ = [
    "OllamaManager",
    "scan_root",
    "find_ollama",
    "discover_models_dir",
    "list_models",
    "probe",
    "probe_ps",
    "probe_model",
    "server_url",
    "tunnel_url",
    "load_settings",
    "save_settings",
    "active_embed_config",
]

# Служебные папки, которые не нужно обходить при поиске ollama.exe.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "installer_files",
    "user_data", "blobs", "manifests", "manifests-v2", "models", "lib",
    "extensions", "cache", "logs", "temp", "tmp",
}


# ---------------------------------------------------------------------------
# Платформенные помощники
# ---------------------------------------------------------------------------
def _is_windows():
    return os.name == "nt"


def _exe_names():
    """Имена исполняемого файла Ollama для текущей ОС."""
    return ("ollama.exe", "ollama") if _is_windows() else ("ollama",)


def _norm(path):
    return os.path.abspath(os.path.expanduser(str(path or "")))


def _looks_like_store(path):
    """True, если path похож на хранилище моделей Ollama."""
    if not path or not os.path.isdir(path):
        return False
    has_manifests = (os.path.isdir(os.path.join(path, "manifests"))
                     or os.path.isdir(os.path.join(path, "manifests-v2")))
    return bool(has_manifests and os.path.isdir(os.path.join(path, "blobs")))


# ---------------------------------------------------------------------------
# Поиск установки и хранилища моделей
# ---------------------------------------------------------------------------
def find_ollama(root):
    """Ищет ollama(.exe) в корне (сам корень, ``<root>/ollama``, вглубь до 3).

    Возвращает абсолютный путь или None. Глубина ограничена, служебные
    папки (manifests/blobs/lib/installer_files) пропускаются.
    """
    root = _norm(root)
    if not root or not os.path.isdir(root):
        return None
    wanted = {n.lower() for n in _exe_names()}

    candidates = [os.path.join(root, n) for n in _exe_names()]
    candidates += [os.path.join(root, "ollama", n) for n in _exe_names()]
    for path in candidates:
        if os.path.isfile(path):
            return path

    queue = [(root, 0)]
    while queue:
        dirpath, depth = queue.pop(0)
        if depth > 3:
            continue
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file() and entry.name.lower() in wanted:
                    return entry.path
                if entry.is_dir() and entry.name.lower() not in _SKIP_DIRS:
                    queue.append((entry.path, depth + 1))
            except OSError:
                continue
    # Глобальная установка (VPS/ПК): ollama в PATH.
    for name in _exe_names():
        found = shutil.which(name)
        if found:
            return _norm(found)
    return None


def discover_models_dir(root, exe=None):
    """Ищет каталог моделей Ollama (manifests/blobs) рядом с установкой.

    Учитывает OLLAMA_MODELS (и его подпапку ``models``), расположение
    ``ollama.exe``, сам корень и ``~/.ollama/models``. Дополнительно
    просматривает дерево до глубины 4 — на случай нестандартной раскладки
    (например, ``E:\\Models\\ollama\\models``).
    """
    root = _norm(root)
    candidates = []

    # Сначала — рядом с указанным корнем/исполняемым файлом: пользователь
    # сканирует ИМЕННО эту папку, глобальные ENV не должны её подменять.
    if exe:
        exe_dir = os.path.dirname(_norm(exe))
        candidates += [exe_dir, os.path.join(exe_dir, "models")]
    if root:
        candidates += [root, os.path.join(root, "models")]
    env_models = str(os.environ.get("OLLAMA_MODELS") or "").strip()
    if env_models:
        candidates += [env_models, os.path.join(env_models, "models")]
    candidates.append(os.path.join(os.path.expanduser("~"), ".ollama",
                                   "models"))

    seen = set()
    for path in candidates:
        path = _norm(path)
        if path in seen:
            continue
        seen.add(path)
        if _looks_like_store(path):
            return path

    if not root or not os.path.isdir(root):
        return ""
    queue = [(root, 0)]
    while queue:
        dirpath, depth = queue.pop(0)
        if depth > 4:
            continue
        if _looks_like_store(dirpath):
            return dirpath
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            continue
        for entry in entries:
            try:
                if (entry.is_dir()
                        and entry.name.lower() not in _SKIP_DIRS
                        and entry.name.lower() not in ("blobs", "manifests",
                                                       "manifests-v2")):
                    queue.append((entry.path, depth + 1))
            except OSError:
                continue
    return ""


def _model_from_parts(parts):
    """Собирает имя модели из частей пути манифеста.

    registry.ollama.ai/library/nomic-embed-text/latest -> nomic-embed-text:latest
    host.example/ns/model/tag -> host.example/ns/model:tag
    """
    if len(parts) < 2:
        return ""
    tag = parts[-1]
    model = parts[-2]
    namespace = parts[-3] if len(parts) >= 3 else "library"
    registry = "/".join(parts[:-3]) if len(parts) >= 4 else ""
    if not registry or (registry == "registry.ollama.ai"
                        and namespace == "library"):
        return "%s:%s" % (model, tag)
    return "%s/%s/%s:%s" % (registry, namespace, model, tag)


def _embedding_guess(name):
    low = str(name or "").lower()
    return any(h in low for h in
               getattr(config, "EMBED_MODEL_HINTS", ("embed",)))


def same_model(a, b):
    """Сравнивает имена моделей Ollama (тег может быть опущен).

    ``nomic-embed-text-v2-moe`` и ``nomic-embed-text-v2-moe:latest`` — одна
    и та же модель для пользователя; тег по умолчанию — ``latest``.
    """
    a = str(a or "").strip()
    b = str(b or "").strip()
    if not a or not b:
        return False
    return a == b or a.split(":", 1)[0] == b.split(":", 1)[0]


def list_models(models_dir):
    """Читает список моделей из манифестов (без запуска Ollama).

    Возвращает список dict: {name, tag, size, size_mb, embedding, path,
    modified}. Ошибки отдельных манифестов пропускаются.
    """
    models_dir = str(models_dir or "")
    out = []
    if not models_dir:
        return out
    root = ""
    for sub in ("manifests", "manifests-v2"):
        candidate = os.path.join(models_dir, sub)
        if os.path.isdir(candidate):
            root = candidate
            break
    if not root:
        return out

    seen = set()
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            name = _model_from_parts(rel.split("/"))
            if not name or name in seen:
                continue
            seen.add(name)
            size = 0
            try:
                with open(full, encoding="utf-8") as fh:
                    data = json.load(fh) or {}
                for layer in data.get("layers") or []:
                    if isinstance(layer, dict):
                        size += int(layer.get("size") or 0)
            except Exception:                      # noqa: BLE001
                pass
            try:
                mtime = os.path.getmtime(full)
                modified = time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(mtime))
            except OSError:
                modified = ""
            out.append({
                "name": name,
                "tag": name.rsplit(":", 1)[-1],
                "size": int(size),
                "size_mb": int(size / (1024 * 1024)) if size else 0,
                "embedding": _embedding_guess(name),
                "path": full,
                "modified": modified,
            })
    out.sort(key=lambda m: (not m.get("embedding"),
                            m.get("name", "").lower()))
    return out


def scan_root(root):
    """Сканирует корень: Ollama, каталог моделей, установленные модели.

    Возвращает dict {ok, root, exists, servers, models, models_dir, errors}.
    """
    root = str(root or "").strip().strip('"')
    result = {"ok": False, "root": root, "exists": False,
              "servers": [], "models": [], "models_dir": "", "errors": []}
    if not root:
        result["errors"].append("путь не указан")
        return result
    if not os.path.isdir(root):
        result["errors"].append("папка не найдена: %s" % root)
        return result
    result["exists"] = True

    exe = find_ollama(root)
    if exe:
        result["servers"].append({
            "kind": "ollama",
            "path": exe,
            "name": os.path.basename(exe),
        })
    models_dir = discover_models_dir(root, exe)
    result["models_dir"] = models_dir
    models = list_models(models_dir)
    if not models and exe:
        result["errors"].append(
            "каталог моделей Ollama не найден (нет manifests/blobs)")
    result["models"] = models
    result["ok"] = bool(exe or models)
    return result


# ---------------------------------------------------------------------------
# HTTP-пробы
# ---------------------------------------------------------------------------
def server_url(host=None, port=None):
    """Базовый URL локальной Ollama (без /api)."""
    return "http://%s:%d" % (host or config.EMBED_LOCAL_HOST,
                             int(port or config.EMBED_LOCAL_PORT))


def tunnel_url():
    """URL Ollama ПК, видимый на VPS через reverse-SSH-туннель."""
    return "http://%s:%d" % (config.EMBED_TUNNEL_HOST,
                             int(config.EMBED_TUNNEL_PORT))


def _http_get_json(url, timeout=None):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(
            req, timeout=timeout or config.EMBED_PROBE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url, payload, timeout=None):
    headers = {"Content-Type": "application/json"}
    key = str(getattr(config, "RAG_EMBED_API_KEY", "") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(
            req, timeout=timeout or config.RAG_EMBED_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_tags(data):
    """Преобразует ответ /api/tags в список моделей с возможностями."""
    out = []
    for item in (data or {}).get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        details = item.get("details") or {}
        caps = [str(c) for c in (item.get("capabilities") or [])]
        dim = details.get("embedding_length")
        try:
            dim = int(dim) if dim else None
        except (TypeError, ValueError):
            dim = None
        size = int(item.get("size") or 0)
        out.append({
            "name": name,
            "size": size,
            "size_mb": int(size / (1024 * 1024)),
            "capabilities": caps,
            "embedding": ("embedding" in caps) or _embedding_guess(name),
            "embedding_length": dim,
            "family": details.get("family") or "",
            "parameter_size": details.get("parameter_size") or "",
            "quantization": details.get("quantization_level") or "",
        })
    return out


def probe(url=None, timeout=None):
    """Проверяет Ollama (GET /api/tags) и отдаёт модели.

    Возвращает {alive, models, error, url}. Лёгкая проверка: модель не
    загружается в память.
    """
    base = (url or server_url()).rstrip("/")
    try:
        data = _http_get_json(base + "/api/tags", timeout=timeout)
        return {"alive": True, "models": _parse_tags(data),
                "error": None, "url": base}
    except Exception as exc:                       # noqa: BLE001
        return {"alive": False, "models": [], "error": str(exc),
                "url": base}


def probe_ps(url=None, timeout=None):
    """Список моделей, ЗАГРУЖЕННЫХ в память (GET /api/ps)."""
    base = (url or server_url()).rstrip("/")
    try:
        data = _http_get_json(base + "/api/ps", timeout=timeout)
        out = []
        for item in (data or {}).get("models") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                out.append({"name": name,
                            "size": int(item.get("size") or 0),
                            "until": item.get("expires_at") or ""})
        return {"alive": True, "models": out, "error": None}
    except Exception as exc:                       # noqa: BLE001
        return {"alive": False, "models": [], "error": str(exc)}


def _extract_vector(data):
    """Достаёт вектор из ответа Ollama/OpenAI (несколько форматов)."""
    if not isinstance(data, dict):
        return []
    vec = data.get("embedding")
    if isinstance(vec, list) and vec:
        return vec
    embeddings = data.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, list) and first:
            return first
    items = data.get("data")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        vec = items[0].get("embedding")
        if isinstance(vec, list) and vec:
            return vec
    return []


def probe_model(name, url=None, timeout=None):
    """Считает тестовый эмбеддинг для модели и возвращает размерность.

    Сначала используется /api/embeddings (как в rag.embed_text), при 404/405 —
    /api/embed. Возвращает {ok, model, dim, api, error, loaded}.
    """
    base = (url or server_url()).rstrip("/")
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "model": "", "dim": 0, "api": "",
                "error": "модель не указана", "loaded": False}
    attempts = [
        ("ollama", base + "/api/embeddings",
         {"model": name, "prompt": "проверка"}),
        ("ollama-embed", base + "/api/embed",
         {"model": name, "input": ["проверка"]}),
        ("openai", base + "/v1/embeddings",
         {"model": name, "input": "проверка"}),
    ]
    last_error = None
    for api, endpoint, payload in attempts:
        try:
            data = _http_post_json(endpoint, payload, timeout=timeout)
            vec = _extract_vector(data)
            if vec:
                ps = probe_ps(base, timeout=timeout)
                loaded = any(same_model(m["name"], name)
                             for m in ps.get("models") or [])
                return {"ok": True, "model": name, "dim": len(vec),
                        "api": api, "error": None, "loaded": loaded}
        except urllib.error.HTTPError as exc:
            last_error = "HTTP %s" % exc.code
        except Exception as exc:                    # noqa: BLE001
            last_error = str(exc)
    return {"ok": False, "model": name, "dim": 0, "api": "",
            "error": last_error or "эмбеддинг не получен", "loaded": False}


# ---------------------------------------------------------------------------
# Настройки/активный движок эмбеддингов
# ---------------------------------------------------------------------------
def load_settings(path=None):
    """Читает настройки блока эмбеддингов (или значения по умолчанию)."""
    path = path or config.EMBED_SETTINGS_FILE
    default_root = getattr(config, "EMBED_LOCAL_ROOT", "") or ""
    if default_root and not os.path.isdir(default_root):
        # Локальная разработка на ПК: /opt/models отсутствует — показываем
        # корень ПК из ENV (RTK_PC_EMBED_ROOT), если он существует.
        pc_root = getattr(config, "EMBED_PC_ROOT", "") or ""
        if pc_root and os.path.isdir(pc_root):
            default_root = pc_root
    default = {
        "enabled": bool(getattr(config, "EMBED_ENABLED", False)),
        "managed": False,          # True — блок сам управлял Ollama
        "root": default_root,
        "exe": "",
        "models_dir": "",
        "model": "",
        "backend": "ollama",
        "api": "ollama",
        "url": "",
        "dim": int(getattr(config, "RAG_EMBED_DIM", 768) or 768),
        "source": "vps",           # vps | pc
        "reuse": bool(getattr(config, "EMBED_REUSE_RUNNING", True)),
    }
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh) or {}
        if isinstance(data, dict):
            default.update(data)
    except Exception:                              # noqa: BLE001
        pass
    return default


def save_settings(settings, path=None):
    """Сохраняет настройки блока (best-effort)."""
    path = path or config.EMBED_SETTINGS_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(settings or {}, fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def active_embed_config():
    """Активная конфигурация эмбеддингов для rag.embed_text/status.

    * файла настроек нет -> значения из ENV/config (обратная совместимость);
    * файл есть и модель включена -> выбранный Ollama-движок;
    * файл есть, но движок остановлен -> backend="none" (явный отказ, а не
      молчаливый возврат к ENV-адресу).
    """
    path = config.EMBED_SETTINGS_FILE
    has_file = bool(path) and os.path.isfile(path)
    settings = load_settings(path)
    if has_file and settings.get("managed"):
        if not settings.get("enabled") or settings.get("backend") == "none":
            return {"backend": "none", "api": "", "url": "",
                    "model": settings.get("model") or "",
                    "dim": int(settings.get("dim") or config.RAG_EMBED_DIM),
                    "api_key": "", "source": settings.get("source") or "vps",
                    "models_dir": settings.get("models_dir") or "",
                    "exe": settings.get("exe") or "",
                    "root": settings.get("root") or ""}
        return {
            "backend": "ollama",
            "api": settings.get("api") or "ollama",
            "url": settings.get("url") or config.RAG_EMBED_URL,
            "model": settings.get("model") or config.RAG_EMBED_MODEL,
            "dim": int(settings.get("dim") or config.RAG_EMBED_DIM),
            "api_key": str(getattr(config, "RAG_EMBED_API_KEY", "") or ""),
            "source": settings.get("source") or "vps",
            "models_dir": settings.get("models_dir") or "",
            "exe": settings.get("exe") or "",
            "root": settings.get("root") or "",
        }
    # Обратная совместимость: блок ещё не использовался.
    return {
        "backend": str(getattr(config, "RAG_EMBED_API", "ollama")
                       or "ollama"),
        "api": str(getattr(config, "RAG_EMBED_API", "ollama") or "ollama"),
        "url": config.RAG_EMBED_URL,
        "model": config.RAG_EMBED_MODEL,
        "dim": int(config.RAG_EMBED_DIM),
        "api_key": str(getattr(config, "RAG_EMBED_API_KEY", "") or ""),
        "source": "vps",
        "models_dir": "",
        "exe": "",
        "root": "",
    }


# ---------------------------------------------------------------------------
# Поиск PID, слушающего порт (для отображения «внешний процесс»)
# ---------------------------------------------------------------------------
def _find_pid_on_port(port):
    port = int(port or 0)
    if not port:
        return None
    try:
        if _is_windows():
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True,
                text=True, timeout=5).stdout
        else:
            out = subprocess.run(
                ["ss", "-ltnp"], capture_output=True, text=True,
                timeout=5).stdout
    except Exception:                              # noqa: BLE001
        return None
    for line in (out or "").splitlines():
        if ":%d" % port not in line:
            continue
        if _is_windows():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                try:
                    return int(parts[4])
                except ValueError:
                    continue
        else:
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Менеджер процесса Ollama
# ---------------------------------------------------------------------------
class OllamaManager(object):
    """Запуск/остановка/статус Ollama с политикой reuse-first.

    Держит ОДИН процесс, запущенный этим менеджером. Если Ollama уже
    работает снаружи (трей), она не останавливается и не дублируется.
    """

    def __init__(self, settings_file=None, host=None, port=None):
        self.settings_file = settings_file or config.EMBED_SETTINGS_FILE
        self.host = host or config.EMBED_LOCAL_HOST
        self.port = int(port or config.EMBED_LOCAL_PORT)
        self._proc = None
        self._lock = threading.RLock()
        self._log_path = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or ".",
            "rtk_ollama.log")

    # ---- настройки ----
    def load_settings(self):
        return load_settings(self.settings_file)

    def save_settings(self, settings):
        return save_settings(settings, self.settings_file)

    # ---- состояние ----
    def _proc_alive(self):
        return self._proc is not None and self._proc.poll() is None

    def url(self):
        return server_url(self.host, self.port)

    def status(self):
        """Снимок: работает ли Ollama, какие модели есть/загружены."""
        pr = probe(self.url())
        ps = probe_ps(self.url()) if pr.get("alive") else {
            "alive": False, "models": [], "error": None}
        loaded = {m["name"] for m in ps.get("models") or []}
        models = []
        for m in pr.get("models") or []:
            item = dict(m)
            item["loaded"] = item["name"] in loaded
            models.append(item)
        return {
            "running": bool(pr.get("alive")),
            "ready": bool(pr.get("alive")),
            "external": bool(pr.get("alive") and not self._proc_alive()),
            "pid": (self._proc.pid if self._proc_alive()
                    else (_find_pid_on_port(self.port)
                          if pr.get("alive") else None)),
            "url": self.url(),
            "models": models,
            "loaded_models": ps.get("models") or [],
            "error": pr.get("error"),
            "models_dir": self.load_settings().get("models_dir") or "",
            "port": self.port,
        }

    # ---- запуск/остановка ----
    def _wait_ready(self, deadline):
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if probe(self.url(), timeout=2).get("alive"):
                return True
            time.sleep(1.0)
        return False

    def start(self, exe=None, models_dir=None, model=None, reuse=None,
              force_restart=False):
        """Запускает Ollama либо ПЕРЕИСПОЛЬЗУЕТ уже запущенную.

        Возвращает dict:
          {ok, already, reused, external, pid, url, model, dim, models,
           models_dir, error, needs_choice}
        """
        reuse = config.EMBED_REUSE_RUNNING if reuse is None else bool(reuse)
        settings = self.load_settings()
        with self._lock:
            current = probe(self.url())
            if current.get("alive"):
                if not reuse and not force_restart:
                    return {"ok": False, "needs_choice": True,
                            "error": "Ollama уже запущена — второй процесс "
                                     "не поднимаю (reuse выключен)",
                            "models": current.get("models") or [],
                            "url": self.url()}
                models = current.get("models") or []
                names = [m["name"] for m in models]
                chosen = str(model or settings.get("model") or "").strip()
                if not chosen:
                    emb = [m for m in models if m.get("embedding")]
                    chosen = (emb[0]["name"] if emb
                              else (names[0] if names else ""))
                matches = [n for n in names if same_model(n, chosen)]
                if chosen and not matches:
                    return {
                        "ok": False, "needs_choice": True,
                        "error": "в запущенной Ollama нет модели %s — "
                                 "выберите другую из списка" % chosen,
                        "models": models,
                        "embedding_models": [m["name"] for m in models
                                             if m.get("embedding")],
                        "url": self.url(), "external": True,
                    }
                if matches:
                    # Используем фактическое имя с тегом (обычно :latest).
                    chosen = matches[0]
                if not chosen:
                    return {"ok": False, "needs_choice": True,
                            "error": "в запущенной Ollama нет моделей",
                            "models": [], "url": self.url()}
                info = self._record(
                    exe=settings.get("exe") or "",
                    models_dir=settings.get("models_dir") or "",
                    root=settings.get("root") or "",
                    model=chosen,
                    source=settings.get("source") or "vps")
                info.update({"ok": True, "already": True, "reused": True,
                             "external": True, "url": self.url(),
                             "pid": _find_pid_on_port(self.port),
                             "models": models})
                return info

            if force_restart and self._proc_alive():
                self._stop_own()

            # Определяем исполняемый файл и каталог моделей.
            exe = str(exe or settings.get("exe") or "").strip()
            models_dir = str(models_dir or settings.get("models_dir")
                             or "").strip()
            root = str(settings.get("root") or config.EMBED_LOCAL_ROOT or "")
            if not exe or not models_dir:
                scan = scan_root(root)
                exe = exe or (scan.get("servers") or [{}])[0].get("path") or ""
                models_dir = models_dir or scan.get("models_dir") or ""
            if not exe or not os.path.isfile(exe):
                return {"ok": False, "error": "ollama не найдена: %s" % exe,
                        "url": self.url()}
            if not models_dir or not _looks_like_store(models_dir):
                return {"ok": False,
                        "error": "каталог моделей Ollama не найден: %s"
                                 % models_dir,
                        "url": self.url()}

            available = list_models(models_dir)
            names = [m["name"] for m in available]
            chosen = str(model or settings.get("model") or "").strip()
            if not chosen:
                emb = [m for m in available if m.get("embedding")]
                chosen = (emb[0]["name"] if emb
                          else (names[0] if names else ""))
            if chosen and names and chosen not in names:
                return {"ok": False, "needs_choice": True,
                        "error": "модель %s не найдена в %s"
                                 % (chosen, models_dir),
                        "models": available,
                        "url": self.url()}
            if not chosen:
                return {"ok": False,
                        "error": "в каталоге моделей Ollama нет моделей: %s"
                                 % models_dir,
                        "models": available, "url": self.url()}

            env = dict(os.environ)
            env["OLLAMA_MODELS"] = models_dir
            env["OLLAMA_HOST"] = "%s:%d" % (self.host, self.port)
            creationflags = 0
            if _is_windows():
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                log = open(self._log_path, "ab")
                self._proc = subprocess.Popen(
                    [exe, "serve"], cwd=os.path.dirname(exe) or None,
                    stdout=log, stderr=subprocess.STDOUT, env=env,
                    creationflags=creationflags)
                log.close()
            except Exception as exc:                # noqa: BLE001
                self._proc = None
                return {"ok": False,
                        "error": "не удалось запустить Ollama: %s" % exc,
                        "url": self.url()}

            deadline = time.time() + int(config.EMBED_START_TIMEOUT)
            if not self._wait_ready(deadline):
                self._stop_own()
                return {"ok": False,
                        "error": "Ollama не ответила за %d с"
                                 % int(config.EMBED_START_TIMEOUT),
                        "url": self.url()}
            current = probe(self.url())
            live_names = [m["name"] for m in current.get("models") or []]
            live_matches = [n for n in live_names if same_model(n, chosen)]
            if chosen and not live_matches:
                self._stop_own()
                return {"ok": False, "needs_choice": True,
                        "error": "модель %s не появилась в Ollama" % chosen,
                        "models": current.get("models") or [],
                        "url": self.url()}
            if live_matches:
                chosen = live_matches[0]
            info = self._record(exe=exe, models_dir=models_dir, root=root,
                                model=chosen,
                                source=settings.get("source") or "vps")
            info.update({"ok": True, "already": False, "reused": False,
                         "external": False, "url": self.url(),
                         "pid": self._proc.pid if self._proc else None,
                         "models": current.get("models") or []})
            return info

    def _record(self, exe, models_dir, root, model, source):
        """Фиксирует выбранную модель как активный движок эмбеддингов."""
        models = probe(self.url()).get("models") or []
        dim = None
        for m in models:
            if same_model(m.get("name"), model):
                model = m.get("name") or model
                dim = m.get("embedding_length")
                break
        settings = self.load_settings()
        settings.update({
            "enabled": True,
            "managed": True,
            "backend": "ollama",
            "api": "ollama",
            "url": self.url() + "/api/embeddings",
            "model": model,
            "dim": int(dim or settings.get("dim")
                       or config.RAG_EMBED_DIM),
            "source": source or settings.get("source") or "vps",
            "exe": exe or settings.get("exe") or "",
            "models_dir": models_dir or settings.get("models_dir") or "",
            "root": root or settings.get("root") or "",
        })
        self.save_settings(settings)
        return {"model": model, "dim": settings["dim"],
                "source": settings["source"]}

    def _stop_own(self):
        if not self._proc_alive():
            self._proc = None
            return False
        try:
            if _is_windows():
                subprocess.run(["taskkill", "/PID", str(self._proc.pid),
                                "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
            else:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        except Exception:                          # noqa: BLE001
            pass
        finally:
            self._proc = None
        return True

    def stop(self):
        """Останавливает ТОЛЬКО свой процесс; внешнюю Ollama не трогает."""
        with self._lock:
            stopped = self._stop_own()
            if stopped:
                self._mark_stopped()
                return {"ok": True, "stopped": True, "external": False,
                        "url": self.url()}
            if probe(self.url()).get("alive"):
                # Внешняя Ollama продолжает работать — настройки НЕ трогаем,
                # чтобы её можно было использовать и дальше.
                return {"ok": False, "stopped": False, "external": True,
                        "error": "Ollama запущена внешним процессом — "
                                 "используется как есть, не останавливаю",
                        "url": self.url()}
            self._mark_stopped()
            return {"ok": True, "stopped": False, "external": False,
                    "url": self.url()}

    def _mark_stopped(self):
        """Помечает активный движок как остановленный (backend=none)."""
        settings = self.load_settings()
        settings["enabled"] = False
        settings["managed"] = True
        settings["backend"] = "none"
        settings["url"] = ""
        self.save_settings(settings)

    def scan(self, root=None):
        """Скан корня + текущий статус (для UI)."""
        root = str(root or self.load_settings().get("root")
                   or config.EMBED_LOCAL_ROOT or "")
        result = scan_root(root)
        status = self.status()
        running = {m["name"]: m for m in status.get("models") or []}
        for m in result.get("models") or []:
            live = running.get(m["name"])
            if live:
                m["embedding"] = bool(live.get("embedding"))
                m["embedding_length"] = live.get("embedding_length")
                m["capabilities"] = live.get("capabilities") or []
                m["loaded"] = bool(live.get("loaded"))
        return result








