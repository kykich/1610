# -*- coding: utf-8 -*-
"""ПК-АГЕНТ (Задача 1): локальная сильная LLM с ПК оператора через SSH-туннель.

Назначение
----------
Этот модуль запускается НА ПК ОПЕРАТОРА (Windows). Он:

  1) держит ПОСТОЯННЫЙ reverse-SSH-туннель к VPS, пробрасывая движок ПК на
     loopback VPS:
         ПК:  llama-server слушает 127.0.0.1:8080 (сильная модель Qwen-9B)
         ПК:  ssh -N -R 127.0.0.1:18080:127.0.0.1:8080 rtk@<VPS>
         VPS: движок ПК виден как 127.0.0.1:18080 (сильная модель);
     вторым -R пробрасывается и СЛУЖЕБНЫЙ ПОРТ агента (18081), чтобы rtk-web
     на VPS мог попросить агента просканировать папку и узнать статус
     подтверждения оператора;

  2) показывает ОКНО-ПОДТВЕРЖДЕНИЕ (один раз на SSH-сессию) — оператор должен
     подтвердить, что ВПС (rtk-web) вправе использовать движок его ПК;

  3) поднимает небольшой служебный HTTP-API (на 127.0.0.1:18081), доступный
     VPS только через туннель:
         POST /scan           {"root": "C:\\models"}  -> список моделей/серверов
         GET  /confirm_status                         -> {"confirmed": bool}
         POST /confirm        {"ok": true}            -> подтвердить
         GET  /health                                 -> {"ok": true}

Требования: Python 3.8+ (стандартная библиотека), OpenSSH-клиент (ssh.exe) в
PATH, запущенный llama-server ПК (сильная модель).

Запуск (пример):
    python -m rtk_pc_agent --vps rtk@example.com --port 22 \\
        --engine-port 8080 --tunnel-port 18080 --agent-port 18081 \\
        --root C:\\models

См. docs/task1-local-llm-from-pc.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Сканирование папки НА ПК
# ---------------------------------------------------------------------------
# Аналогично rtk_app.local_server.scan_folder, но выполняется на ПК и не тя-
# нет тяжёлых зависимостей: ищем исполняемые файлы серверов (llama-server.exe,
# ollama.exe, lmstudio) и файлы моделей (*.gguf, *.bin, blobs Ollama).
_SERVER_NAMES = (
    "llama-server", "llama_server", "server", "ollama",
    "lmstudio", "lm-studio", "koboldcpp",
)
_MODEL_EXTS = (".gguf", ".bin", ".safetensors")


def _scan_folder(root):
    """Сканирует папку ПК: возвращает {models:[...], servers:[...]}.

    Структура элементов совпадает с ожидаемой rtk-web:
      servers: {kind, name, path}
      models:  {name, path, size_mb}
    """
    root = os.path.abspath(os.path.expanduser(root or ""))
    result = {"models": [], "servers": [], "root": root}
    if not root or not os.path.isdir(root):
        result["error"] = "папка не найдена: %s" % root
        return result

    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            low = fn.lower()
            full = os.path.join(dirpath, fn)
            stem = os.path.splitext(low)[0]
            # Серверы (исполняемые файлы).
            if stem in _SERVER_NAMES and low.endswith((".exe", "")):
                kind = "ollama" if "ollama" in stem else (
                    "lmstudio" if "lmstudio" in stem or "lm-studio" in stem
                    else "llama.cpp")
                result["servers"].append(
                    {"kind": kind, "name": fn, "path": full})
            # Модели (*.gguf и пр.).
            elif low.endswith(_MODEL_EXTS):
                try:
                    size_mb = int(os.path.getsize(full) // (1024 * 1024))
                except OSError:
                    size_mb = 0
                result["models"].append(
                    {"name": fn, "path": full, "size_mb": size_mb})
    # Стабильный порядок — детерминированный ответ.
    result["servers"].sort(key=lambda s: s["path"])
    result["models"].sort(key=lambda m: m["path"])
    return result


# ---------------------------------------------------------------------------
# Окно-подтверждение оператора (один раз на SSH-сессию)
# ---------------------------------------------------------------------------
class _ConfirmState:
    """Потокобезопасное состояние подтверждения оператора."""

    def __init__(self):
        self._lock = threading.Lock()
        self.confirmed = False
        self.denied = False

    def set(self, confirmed):
        with self._lock:
            self.confirmed = bool(confirmed)
            self.denied = not confirmed

    def get(self):
        with self._lock:
            return self.confirmed, self.denied


def _show_confirm_dialog(vps, engine_port, tunnel_port, state,
                         ollama_port=None, embed_port=None):
    """Показывает модальное окно подтверждения (Windows: ctypes MessageBox)."""
    extra = ""
    if ollama_port and embed_port:
        extra = (
            "\nOllama (порт %d, эмбеддинги RAG) будет доступна VPS как\n"
            "loopback VPS:127.0.0.1:%d.\n" % (ollama_port, embed_port))
    text = (
        "VPS (%s) запрашивает доступ к ЛОКАЛЬНОЙ СИЛЬНОЙ LLM на этом ПК.\n\n"
        "Движок ПК (порт %d) будет временно доступен VPS через SSH-туннель\n"
        "(loopback VPS:127.0.0.1:%d). Модель ФИЗИЧЕСКИ остаётся на вашем ПК.\n"
        "%s\nРазрешить использование сильной модели и Ollama на время "
        "этой сессии?"
        % (vps or "(VPS)", engine_port, tunnel_port, extra))
    title = "RTK: подтвердите доступ к локальной LLM"
    try:
        import ctypes  # только Windows
        MB_YESNO = 0x04
        MB_ICONQUESTION = 0x20
        MB_SETFOREGROUND = 0x10000
        MB_TOPMOST = 0x40000
        res = ctypes.windll.user32.MessageBoxW(
            None, text, title,
            MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST)
        state.set(res == 6)   # 6 == IDYES
    except Exception:          # noqa: BLE001 — не Windows / нет GUI
        # Запасной вариант: подтверждение в консоли.
        sys.stderr.write(text + "\n[yes/no]: ")
        sys.stderr.flush()
        try:
            ans = input().strip().lower()
        except EOFError:
            ans = "no"
        state.set(ans in ("y", "yes", "д", "да"))


def _maybe_confirm(vps, engine_port, tunnel_port, state, required,
                   ollama_port=None, embed_port=None):
    """Показывает окно подтверждения, если оператор ещё не отвечал.

    required=True  — ждём ответа перед поднятием туннеля (безопасно);
    required=False — окно показывается, но туннель не блокируется.
    """
    confirmed, denied = state.get()
    if confirmed or denied:
        return confirmed
    _show_confirm_dialog(vps, engine_port, tunnel_port, state,
                         ollama_port=ollama_port, embed_port=embed_port)
    confirmed, _ = state.get()
    return confirmed


# ---------------------------------------------------------------------------
# Reverse-SSH-туннель
# ---------------------------------------------------------------------------
def _ssh_tunnel_cmd(vps, port, engine_port, tunnel_port, agent_port,
                    identity=None, ollama_port=None, embed_tunnel_port=None):
    """Собирает argv для ssh с -R: движок + API агента + Ollama (эмбеддинги)."""
    cmd = [
        "ssh", "-N",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
    ]
    if port:
        cmd += ["-p", str(port)]
    if identity:
        cmd += ["-i", identity]
    # Движок ПК -> loopback VPS:tunnel_port.
    cmd += ["-R", "127.0.0.1:%d:127.0.0.1:%d" % (tunnel_port, engine_port)]
    # Служебный API агента ПК -> loopback VPS:agent_port.
    cmd += ["-R", "127.0.0.1:%d:127.0.0.1:%d" % (agent_port, agent_port)]
    # Ollama ПК (эмбеддинги RAG) -> loopback VPS:embed_tunnel_port.
    if ollama_port and embed_tunnel_port:
        cmd += ["-R", "127.0.0.1:%d:127.0.0.1:%d"
                % (embed_tunnel_port, ollama_port)]
    cmd.append(vps)
    return cmd


def _ollama_status(port=11434, timeout=3):
    """Проверяет Ollama на ПК и возвращает список моделей (GET /api/tags)."""
    url = "http://127.0.0.1:%d/api/tags" % int(port or 11434)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [str(it.get("name") or it.get("model") or "")
                 for it in (data.get("models") or []) if isinstance(it, dict)]
        return {"alive": True, "models": [n for n in names if n],
                "error": None, "url": url}
    except Exception as exc:                       # noqa: BLE001
        return {"alive": False, "models": [], "error": str(exc), "url": url}


class _EngineKeeper:
    """Запуск/остановка llama-server НА ПК по команде с VPS."""

    def __init__(self, port=8080, timeout=180, root=""):
        self.port = int(port or 8080)
        self.timeout = int(timeout or 180)
        self.root = str(root or "")
        self.proc = None
        self._lock = threading.Lock()
        self.log_path = os.path.join(tempfile.gettempdir(),
                                     "rtk_llama_server.log")

    def _url(self):
        return "http://127.0.0.1:%d/v1" % self.port

    def _probe(self):
        try:
            req = urllib.request.Request(self._url() + "/models",
                                         method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:                          # noqa: BLE001
            return False

    def _models(self):
        try:
            req = urllib.request.Request(self._url() + "/models",
                                         method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [str(it.get("id")) for it in (data.get("data") or [])
                    if isinstance(it, dict) and it.get("id")]
        except Exception:                          # noqa: BLE001
            return []

    def _under_root(self, path):
        if not self.root:
            return True
        root = os.path.abspath(os.path.expanduser(self.root))
        full = os.path.abspath(os.path.expanduser(path))
        try:
            return os.path.commonpath([root, full]) == root
        except ValueError:
            return False

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def status(self):
        ready = self._probe()
        return {"running": bool(self._alive() or ready),
                "pid": (self.proc.pid if self._alive() else None),
                "ready": bool(ready),
                "models": self._models() if ready else [],
                "url": self._url(), "port": self.port}

    def tail(self, max_chars=2000):
        try:
            with open(self.log_path, "rb") as fh:
                data = fh.read()[-max_chars:]
            return data.decode("utf-8", "replace")
        except Exception:                          # noqa: BLE001
            return ""


    def start(self, server_path, params):
        params = dict(params or {})
        with self._lock:
            if self._alive():
                return {"ok": True, "already": True,
                        "pid": self.proc.pid, "url": self._url()}
            if self._probe():
                return {"ok": True, "already": True, "pid": None,
                        "url": self._url()}
            server_path = str(server_path or "").strip()
            model = str(params.get("model") or "").strip()
            if not server_path or not os.path.isfile(server_path):
                return {"ok": False, "error": "сервер не найден: %s"
                                              % server_path}
            if not model or not os.path.isfile(model):
                return {"ok": False, "error": "модель не найдена: %s"
                                              % model}
            if not self._under_root(server_path) or not self._under_root(model):
                return {"ok": False,
                        "error": "путь вне разрешённой папки моделей"}
            cmd = [server_path, "-m", model,
                   "--host", "127.0.0.1", "--port", str(self.port)]
            try:
                cmd += ["-c", str(int(params.get("ctx") or 8192))]
            except (TypeError, ValueError):
                cmd += ["-c", "8192"]
            for key, flag in (("ngl", "-ngl"), ("threads", "-t")):
                try:
                    cmd += [flag, str(int(params.get(key)))]
                except (TypeError, ValueError):
                    pass
            fa = str(params.get("flash_attn") or "auto").lower()
            if fa in ("on", "off", "auto"):
                cmd += ["-fa", fa]
            if params.get("jinja"):
                cmd += ["--jinja"]
            key = str(params.get("api_key") or "").strip()
            if key:
                cmd += ["--api-key", key]
            try:
                log = open(self.log_path, "ab")
                self.proc = subprocess.Popen(
                    cmd, cwd=os.path.dirname(server_path) or None,
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess,
                                          "CREATE_NO_WINDOW", 0))
            except Exception as exc:               # noqa: BLE001
                return {"ok": False, "error": "не удалось запустить: %s"
                                              % exc}
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    return {"ok": False, "error": "процесс завершился",
                            "log": self.tail()}
                if self._probe():
                    return {"ok": True, "pid": self.proc.pid,
                            "url": self._url(), "model": model}
                time.sleep(1.0)
            self.stop()
            return {"ok": False, "error": "сервер не ответил за %d с"
                                          % self.timeout,
                    "log": self.tail()}

    def stop(self):
        with self._lock:
            if self.proc is None:
                return False
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
            except Exception:                      # noqa: BLE001
                pass
            finally:
                self.proc = None
            return True


class TunnelKeeper:
    """Держит ssh-туннель живым: перезапускает при обрыве."""

    def __init__(self, args, state, stop_event):
        self.args = args
        self.state = state
        self.stop = stop_event
        self.proc = None

    def run(self):
        while not self.stop.is_set():
            # Ждём подтверждения оператора перед каждым поднятием туннеля.
            if not _maybe_confirm(self.args.vps, self.args.engine_port,
                                  self.args.tunnel_port, self.state,
                                  required=True,
                                  ollama_port=self.args.ollama_port,
                                  embed_port=self.args.embed_tunnel_port):
                print("[PC-AGENT] оператор не подтвердил доступ — "
                      "туннель не поднимается. Ожидание…", flush=True)
                self.stop.wait(10)
                continue
            cmd = _ssh_tunnel_cmd(
                self.args.vps, self.args.port, self.args.engine_port,
                self.args.tunnel_port, self.args.agent_port,
                self.args.identity,
                ollama_port=self.args.ollama_port,
                embed_tunnel_port=self.args.embed_tunnel_port)
            print("[PC-AGENT] поднимаю туннель: %s" % " ".join(cmd),
                  flush=True)
            try:
                self.proc = subprocess.Popen(cmd)
                self.proc.wait()
            except FileNotFoundError:
                print("[PC-AGENT] ssh не найден в PATH. Установите OpenSSH.",
                      flush=True)
                self.stop.wait(30)
                continue
            except Exception as exc:               # noqa: BLE001
                print("[PC-AGENT] ошибка туннеля: %s" % exc, flush=True)
            if self.stop.is_set():
                break
            print("[PC-AGENT] туннель оборвался — перезапуск через 5 с.",
                  flush=True)
            self.stop.wait(5)
        self._terminate()

    def _terminate(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:                      # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Служебный HTTP-API агента (доступен VPS ТОЛЬКО через туннель)
# ---------------------------------------------------------------------------
def make_handler(args, state, engine=None):
    """Фабрика обработчика HTTP-API агента."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "rtk-pc-agent/1.0"

        def log_message(self, fmt, *a):            # тише в консоли
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                return json.loads(raw.decode("utf-8") or "{}")
            except Exception:                      # noqa: BLE001
                return {}

        def do_GET(self):                          # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/health":
                return self._send(200, {"ok": True, "port": args.agent_port})
            if path == "/confirm_status":
                confirmed, denied = state.get()
                return self._send(200, {"ok": True, "alive": True,
                                        "confirmed": confirmed,
                                        "denied": denied})
            if path == "/status":
                confirmed, denied = state.get()
                eng = engine.status() if engine is not None else {
                    "running": False, "ready": False, "models": [],
                    "url": None, "port": args.engine_port}
                ollama = (_ollama_status(args.ollama_port)
                          if args.ollama_port else
                          {"alive": False, "models": [],
                           "error": "выключено", "url": None})
                return self._send(200, {
                    "ok": True, "alive": True,
                    "confirmed": confirmed, "denied": denied,
                    "engine": {
                        "port": args.engine_port,
                        "alive": bool(eng.get("ready")),
                        "running": bool(eng.get("running")),
                        "pid": eng.get("pid"),
                        "url": eng.get("url"),
                        "models": eng.get("models") or [],
                        "error": None,
                    },
                    "ollama": ollama,
                    "tunnel": {
                        "engine_port": args.tunnel_port,
                        "agent_port": args.agent_port,
                        "embed_port": args.embed_tunnel_port,
                    },
                })
            if path == "/scan":
                # root можно передать query-параметром (?root=...).
                q = {}
                if "?" in self.path:
                    for part in self.path.split("?", 1)[1].split("&"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            q[k] = v
                root = q.get("root") or args.root or ""
                from urllib.parse import unquote
                return self._send(200, _scan_folder(unquote(root)))
            return self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):                         # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/scan":
                data = self._read_json()
                root = data.get("root") or args.root or ""
                return self._send(200, _scan_folder(root))
            if path == "/params":
                data = self._read_json()
                model = str(data.get("model") or "").strip()
                if not model or not os.path.isfile(model):
                    return self._send(400, {"ok": False,
                                            "error": "модель не найдена"})
                try:
                    from rtk_app.local_server import suggest_params
                    params = suggest_params(model)
                except Exception:                  # noqa: BLE001
                    params = {}
                params.setdefault("model", model)
                params.setdefault("host", "127.0.0.1")
                params.setdefault("port", args.engine_port)
                params.setdefault("ctx", 8192)
                params.setdefault("ngl", 99)
                params.setdefault("threads", 16)
                params.setdefault("flash_attn", "auto")
                params.setdefault("jinja", True)
                return self._send(200, {"ok": True, "params": params})
            if path == "/start":
                data = self._read_json()
                if engine is None:
                    return self._send(503, {"ok": False,
                                            "error": "движок недоступен"})
                res = engine.start(data.get("server_path"),
                                   data.get("params") or {})
                return self._send(200 if res.get("ok") else 400, res)
            if path == "/stop":
                if engine is None:
                    return self._send(503, {"ok": False,
                                            "error": "движок недоступен"})
                stopped = engine.stop()
                return self._send(200, {"ok": True, "stopped": stopped,
                                        "engine": engine.status()})
            if path == "/confirm":
                data = self._read_json()
                state.set(bool(data.get("ok")))
                confirmed, denied = state.get()
                return self._send(200, {"ok": True, "confirmed": confirmed,
                                        "denied": denied})
            return self._send(404, {"ok": False, "error": "not found"})

    return _Handler


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="rtk_pc_agent",
        description="RTK ПК-агент: сильная LLM с ПК через SSH-туннель (Задача 1).")
    p.add_argument("--vps", required=True,
                   help="адрес VPS для ssh (например rtk@example.com)")
    p.add_argument("--port", type=int, default=22,
                   help="SSH-порт VPS (по умолчанию 22)")
    p.add_argument("--identity", default=None,
                   help="путь к приватному SSH-ключу (необязательно)")
    p.add_argument("--engine-port", type=int, default=8080,
                   help="локальный порт llama-server ПК (сильная модель)")
    p.add_argument("--tunnel-port", type=int, default=18080,
                   help="порт движения модели на VPS (loopback)")
    p.add_argument("--agent-port", type=int, default=18081,
                   help="служебный порт агента (loopback, оба конца)")
    p.add_argument("--ollama-port", type=int, default=11434,
                   help="локальный порт Ollama ПК (0 — не пробрасывать)")
    p.add_argument("--embed-tunnel-port", type=int, default=11435,
                   help="loopback-порт Ollama на VPS (эмбеддинги RAG)")
    p.add_argument("--start-timeout", type=int, default=180,
                   help="таймаут запуска llama-server на ПК (секунд)")
    p.add_argument("--root", default="",
                   help="папка ПК с моделями/серверами (по умолчанию — запрос)")
    p.add_argument("--bind", default="127.0.0.1",
                   help="адрес служебного API агента (ТОЛЬКО loopback!)")
    p.add_argument("--no-confirm", action="store_true",
                   help="не показывать окно подтверждения (доверенный режим)")
    p.add_argument("--check", action="store_true",
                   help="только проверить конфигурацию и выйти")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.bind not in ("127.0.0.1", "localhost", "::1"):
        print("[PC-AGENT] ВНИМАНИЕ: служебный API должен слушать ТОЛЬКО "
              "loopback (127.0.0.1). Принудительно ставлю 127.0.0.1.",
              flush=True)
        args.bind = "127.0.0.1"

    state = _ConfirmState()
    if args.no_confirm:
        state.set(True)

    if args.check:
        print("[PC-AGENT] конфигурация:")
        print("  vps         = %s" % args.vps)
        print("  engine-port = %d" % args.engine_port)
        print("  tunnel-port = %d" % args.tunnel_port)
        print("  agent-port  = %d" % args.agent_port)
        print("  ollama-port = %d" % args.ollama_port)
        print("  embed-port  = %d" % args.embed_tunnel_port)
        print("  root        = %s" % (args.root or "(не задан)"))
        if args.ollama_port:
            oll = _ollama_status(args.ollama_port)
            print("  ollama      = %s, моделей=%d"
                  % ("доступна" if oll.get("alive") else "НЕДОСТУПНА",
                     len(oll.get("models") or [])))
        if args.root:
            sc = _scan_folder(args.root)
            print("  найдено: серверов=%d, моделей=%d"
                  % (len(sc["servers"]), len(sc["models"])))
        return 0

    stop = threading.Event()
    engine = _EngineKeeper(args.engine_port, args.start_timeout, args.root)

    # 1) Служебный HTTP-API агента (loopback). VPS достучится только по туннелю.
    handler = make_handler(args, state, engine)
    httpd = ThreadingHTTPServer((args.bind, args.agent_port), handler)
    t_http = threading.Thread(target=httpd.serve_forever, daemon=True)
    t_http.start()
    print("[PC-AGENT] служебный API: http://%s:%d/ (только loopback)"
          % (args.bind, args.agent_port), flush=True)

    # 2) Окно-подтверждение оператора (один раз на сессию).
    if not args.no_confirm:
        confirmed = _maybe_confirm(args.vps, args.engine_port,
                                   args.tunnel_port, state, required=False,
                                   ollama_port=args.ollama_port,
                                   embed_port=args.embed_tunnel_port)
        print("[PC-AGENT] подтверждение оператора: %s"
              % ("да" if confirmed else "ожидается/нет"), flush=True)

    # 3) Держим reverse-туннель (перезапуск при обрыве).
    keeper = TunnelKeeper(args, state, stop)
    print("[PC-AGENT] запускаю туннель к %s…" % args.vps, flush=True)
    try:
        keeper.run()
    except KeyboardInterrupt:
        print("\n[PC-AGENT] остановка по Ctrl+C.", flush=True)
    finally:
        stop.set()
        engine.stop()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
