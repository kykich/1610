# -*- coding: utf-8 -*-
"""Проверка сессионных ключей облачных моделей (DeepSeek / GigaChat).

Ключи вводятся в интерфейсе и живут ТОЛЬКО в памяти вкладки: перезагрузка
страницы, закрытие окна и «новый разговор» их стирают. На сервер они
приходят в теле /api/ask (поле keys) и действуют только на время запроса;
на диск (session/*.json, apidpsk.txt, gigakey.txt) не пишутся.

Проверяется БЕЗ реальной сети (urllib/`_post` подменяются):
  1. нормализация вставленных значений (strip, Bearer, base64 из id:secret);
  2. изоляция ключей по потокам и очистка;
  3. приоритет сессионного ключа над файловым (deepseek/gigachat);
  4. кэш OAuth-токенов GigaChat ПО УЧЁТНЫМ ДАННЫМ;
  5. key_check.check_key (только формат, без сети);
  6. HTTP: GET/POST /api/keys, передача keys в /api/ask, отсутствие утечек
     в ответ и на диск, очистка после запроса, /api/newchat.

Запуск: python -m tests.check_session_keys
"""
import base64
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import config, runtime_keys, deepseek, gigachat, key_check  # noqa: E402
import web.server as srv  # noqa: E402


DS_KEY = "sk-" + "a" * 32
DS_KEY_FILE = "sk-" + "b" * 32
GC_PAIR = "client-id:client-secret"
GC_BASIC = base64.b64encode(GC_PAIR.encode("utf-8")).decode("ascii")


def test_normalize():
    section("1. Нормализация вставленных ключей")
    check("DeepSeek: пробелы по краям сняты",
          runtime_keys.normalize_deepseek("  " + DS_KEY + "  ") == DS_KEY)
    check("DeepSeek: префикс Bearer снят",
          runtime_keys.normalize_deepseek("Bearer " + DS_KEY) == DS_KEY)
    check("DeepSeek: взята первая непустая строка",
          runtime_keys.normalize_deepseek("\n  \n" + DS_KEY + "\nвторая")
          == DS_KEY)
    check("DeepSeek: пустая строка -> ''",
          runtime_keys.normalize_deepseek("   ") == "")
    long_key = "sk-" + "x" * (runtime_keys.MAX_KEY_CHARS + 1)
    raised = False
    try:
        runtime_keys.normalize_deepseek(long_key)
    except ValueError:
        raised = True
    check("DeepSeek: слишком длинный ключ отклонён", raised)

    check("GigaChat: готовая base64 не меняется",
          runtime_keys.normalize_gigachat(GC_BASIC) == GC_BASIC)
    check("GigaChat: пара id:secret кодируется в base64",
          runtime_keys.normalize_gigachat(GC_PAIR) == GC_BASIC)
    check("GigaChat: 'Authorization: Basic …' разбирается",
          runtime_keys.normalize_gigachat(
              "Authorization: Basic " + GC_BASIC) == GC_BASIC)
    check("GigaChat: пустая строка -> ''",
          runtime_keys.normalize_gigachat("  ") == "")
    raised = False
    try:
        runtime_keys.normalize_gigachat("не base64 и не пара")
    except ValueError:
        raised = True
    check("GigaChat: мусор отклонён", raised)

    check("маска DeepSeek: sk-… + 4 символа",
          runtime_keys.mask(DS_KEY) == "sk-…" + DS_KEY[-4:])
    check("маска GigaChat: … + 4 символа",
          runtime_keys.mask(GC_BASIC) == "…" + GC_BASIC[-4:])
    check("маска пустого значения — пусто", runtime_keys.mask("") == "")


def test_thread_isolation():
    section("2. Ключи живут в своём потоке (запросе) и стираются")
    runtime_keys.set_keys(deepseek=DS_KEY)
    check("ключ установлен в текущем потоке",
          runtime_keys.current()["deepseek"] == DS_KEY)
    other = "sk-" + "c" * 32
    seen = {}

    def worker():
        seen["before"] = runtime_keys.current()["deepseek"]
        runtime_keys.set_keys(deepseek=other)
        seen["after"] = runtime_keys.current()["deepseek"]

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    check("другой поток не видит ключ основного", seen["before"] == "")
    check("в другом потоке — свой ключ", seen["after"] == other)
    check("ключ основного потока не изменился",
          runtime_keys.current()["deepseek"] == DS_KEY)
    runtime_keys.clear()
    check("clear() стирает ключи потока",
          runtime_keys.current() == {"deepseek": "", "gigachat": ""})


class _FakeResp(object):
    """Минимальная заглушка urllib-ответа для deepseek.chat."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_deepseek_priority():
    section("3. DeepSeek: сессионный ключ важнее файлового")
    captured = {}
    original = urllib.request.urlopen

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    urllib.request.urlopen = fake_urlopen
    try:
        runtime_keys.set_keys(deepseek=DS_KEY)
        deepseek.chat("file-key", [{"role": "user", "content": "hi"}])
        check("используется ключ из интерфейса",
              captured.get("auth") == "Bearer " + DS_KEY,
              captured.get("auth"))

        deepseek.chat("file-key", [{"role": "user", "content": "hi"}],
                      allow_session=False)
        check("allow_session=False -> переданный ключ (проверка файла)",
              captured.get("auth") == "Bearer file-key",
              captured.get("auth"))

        runtime_keys.clear()
        raised = False
        try:
            deepseek.chat("", [{"role": "user", "content": "hi"}])
        except ValueError as exc:
            raised = "Ключи облачных моделей" in str(exc)
        check("без ключа — понятная ошибка с подсказкой", raised)
    finally:
        urllib.request.urlopen = original
        runtime_keys.clear()


def test_gigachat_tokens():
    section("4. GigaChat: кэш токенов ПО учётным данным")
    calls = []
    original_post = gigachat._post
    original_file = config.GC_KEY_FILE

    def fake_post(url, headers, body):
        calls.append(headers.get("Authorization"))
        return {"access_token": "token-%d" % len(calls)}

    gigachat._post = fake_post
    gigachat._TOKENS.clear()
    config.GC_KEY_FILE = os.path.join(tempfile.mkdtemp(), "no-gigakey.txt")
    try:
        first = gigachat._get_access_token(basic=GC_BASIC,
                                           allow_session=False)
        second = gigachat._get_access_token(basic=GC_BASIC,
                                            allow_session=False)
        check("повторный вызов берёт токен из кэша",
              len(calls) == 1 and first == second, str(calls))
        check("заголовок OAuth содержит Basic-креды",
              calls and calls[0] == "Basic " + GC_BASIC, str(calls))

        other_basic = base64.b64encode(b"other:secret").decode("ascii")
        gigachat._get_access_token(basic=other_basic, allow_session=False)
        check("другие креды -> отдельный запрос токена",
              len(calls) == 2, str(calls))

        calls[:] = []
        runtime_keys.set_keys(gigachat="session:secret")
        session_basic = runtime_keys.current()["gigachat"]
        gigachat._get_access_token(allow_session=True)
        check("сессионные креды важнее файла",
              calls and calls[0] == "Basic " + session_basic, str(calls))

        runtime_keys.clear()
        raised = False
        try:
            gigachat._read_basic_auth()
        except FileNotFoundError as exc:
            raised = "Ключи облачных моделей" in str(exc)
        check("нет файла и ключа — понятная ошибка", raised)
    finally:
        gigachat._post = original_post
        config.GC_KEY_FILE = original_file
        gigachat._TOKENS.clear()
        runtime_keys.clear()

def test_check_key():
    section("5. Проверка вставленного ключа (только формат, без сети)")
    res = key_check.check_key("deepseek", "", network=False)
    check("DeepSeek: пусто -> empty", res["status"] == "empty"
          and res["ok"] is False)
    res = key_check.check_key("deepseek", "abc", network=False)
    check("DeepSeek: короткий ключ -> bad_format",
          res["status"] == "bad_format")
    res = key_check.check_key("deepseek", DS_KEY, network=False)
    check("DeepSeek: корректный формат -> skip (сеть выключена)",
          res["status"] == "skip" and res["masked"] == runtime_keys.mask(DS_KEY))

    res = key_check.check_key("gigachat", "", network=False)
    check("GigaChat: пусто -> empty", res["status"] == "empty")
    res = key_check.check_key("gigachat", "не ключ", network=False)
    check("GigaChat: мусор -> bad_format", res["status"] == "bad_format")
    res = key_check.check_key("gigachat", GC_PAIR, network=False)
    check("GigaChat: пара id:secret принята (после base64)",
          res["status"] == "skip" and res["masked"] == runtime_keys.mask(GC_BASIC))

    raised = False
    try:
        key_check.check_key("other", "x", network=False)
    except ValueError:
        raised = True
    check("неизвестный провайдер -> ValueError", raised)
    check("после проверок сессионные ключи не появились",
          runtime_keys.current() == {"deepseek": "", "gigachat": ""})


class RecordingAgent(object):
    """Фейковый агент: запоминает, какие ключи были в потоке запроса."""

    label = "fake"

    def __init__(self):
        self.seen = []

    def available(self):
        return [{"label": "fake"}]

    def answer(self, question, history, selected, **kwargs):
        self.seen.append(runtime_keys.current())
        return {"ok": True, "text": "ok: " + str(question), "html": "",
                "answers": [{"label": "fake", "model": "fake", "text": "ok"}]}


def make_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj))


def call(opener, url, method="GET", data=None):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def test_http():
    section("6. HTTP: /api/keys, keys в /api/ask, отсутствие утечек")
    tmp = tempfile.mkdtemp()
    old = (config.SESSION_DIR, config.MAX_SESSIONS,
           config.RATE_LIMIT_PER_MIN, config.DS_KEY_FILE, config.GC_KEY_FILE)
    config.SESSION_DIR = tmp
    config.MAX_SESSIONS = 2
    config.RATE_LIMIT_PER_MIN = 0
    # «файловый» ключ DeepSeek — для статуса и маски (полный не отдаётся).
    ds_file = os.path.join(tmp, "apidpsk.txt")
    with open(ds_file, "w", encoding="utf-8") as fh:
        fh.write("# ключ\n" + DS_KEY_FILE + "\n")
    config.DS_KEY_FILE = ds_file
    config.GC_KEY_FILE = os.path.join(tmp, "no-gigakey.txt")

    from web import session_manager as sm
    mgr = sm.SessionManager(max_sessions=2, session_dir=tmp)
    mgr._gc_thread = None
    agent = RecordingAgent()
    httpd = srv.create_server(agent, host="127.0.0.1", port=0)
    srv._ServerState._RTK_VPS_SESSIONS = mgr
    srv._ServerState.session = None
    srv._ServerState.mcp_enabled = False
    port = httpd.server_address[1]
    base = "http://127.0.0.1:%d" % port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        opener = make_opener()
        st, _ = call(opener, base + "/")
        check("страница отдана (200)", st == 200)

        st, body = call(opener, base + "/api/keys")
        j = json.loads(body)
        check("GET /api/keys -> 200", st == 200 and j.get("ok") is True, body)
        check("статус помечен как сессионный (session_only)",
              j.get("session_only") is True)
        check("DeepSeek: файл найден", j["deepseek"]["file"] is True)
        check("DeepSeek: отдаётся только маска",
              j["deepseek"]["masked"] == runtime_keys.mask(DS_KEY_FILE))
        check("полный файловый ключ в ответе отсутствует",
              DS_KEY_FILE not in body)
        check("GigaChat: файла нет", j["gigachat"]["file"] is False)

        payload = {"question": "привет", "models": [{"label": "fake"}],
                   "keys": {"deepseek": DS_KEY, "gigachat": GC_PAIR}}
        st, body = call(opener, base + "/api/ask", "POST", payload)
        check("POST /api/ask с keys -> 200",
              st == 200 and json.loads(body).get("ok") is True, body[:200])
        seen = agent.seen[-1] if agent.seen else {}
        check("агент получил ключ DeepSeek из интерфейса",
              seen.get("deepseek") == DS_KEY, str(seen))
        check("агент получил GigaChat (нормализованный base64)",
              seen.get("gigachat") == GC_BASIC, str(seen))
        check("ключи не попали в ответ API",
              DS_KEY not in body and GC_BASIC not in body)
        check("после ответа ключи потока стёрты",
              runtime_keys.current() == {"deepseek": "", "gigachat": ""},
              str(runtime_keys.current()))

        st, body = call(opener, base + "/api/ask", "POST",
                        {"question": "второй", "models": [{"label": "fake"}]})
        check("второй запрос без keys -> 200", st == 200)
        check("ключи не «протекли» в следующий запрос",
              agent.seen[-1] == {"deepseek": "", "gigachat": ""},
              str(agent.seen[-1]))

        st, body = call(opener, base + "/api/ask", "POST",
                        {"question": "третий", "models": [{"label": "fake"}],
                         "keys": "не объект"})
        check("keys не объект -> запрос без ключей",
              st == 200 and agent.seen[-1]["deepseek"] == "")

        leaks = []
        for root, _dirs, files in os.walk(tmp):
            for name in files:
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                except (UnicodeDecodeError, OSError):
                    continue
                if DS_KEY in text or GC_BASIC in text or GC_PAIR in text:
                    leaks.append(path)
        check("ключи не записаны на диск (session/*.json и др.)",
              not leaks, str(leaks))

        st, body = call(opener, base + "/api/keys", "POST",
                        {"action": "check", "provider": "deepseek",
                         "key": "abc"})
        j = json.loads(body)
        check("check: короткий ключ -> bad_format",
              st == 200 and j.get("status") == "bad_format"
              and j.get("ok") is False, body)
        check("check ничего не сохранил",
              runtime_keys.current()["deepseek"] == "")

        st, _ = call(opener, base + "/api/keys", "POST",
                     {"action": "check", "provider": "other", "key": "x"})
        check("check: неизвестный провайдер -> 400", st == 400)

        st, body = call(opener, base + "/api/keys", "POST",
                        {"action": "status"})
        j = json.loads(body)
        check("POST status -> 200 и маска без ключа",
              st == 200 and bool(j["deepseek"]["masked"])
              and DS_KEY_FILE not in body, body)

        st, _ = call(opener, base + "/api/newchat", "POST", {})
        check("новый разговор -> 200", st == 200)
        st, _ = call(opener, base + "/api/ask", "POST",
                     {"question": "после нового",
                      "models": [{"label": "fake"}]})
        check("после нового разговора ключей нет",
              st == 200 and agent.seen[-1] == {"deepseek": "", "gigachat": ""},
              str(agent.seen[-1]))
    finally:
        httpd.shutdown()
        httpd.server_close()
        (config.SESSION_DIR, config.MAX_SESSIONS, config.RATE_LIMIT_PER_MIN,
         config.DS_KEY_FILE, config.GC_KEY_FILE) = old
        runtime_keys.clear()


def main():
    ensure_utf8()
    test_normalize()
    test_thread_isolation()
    test_deepseek_priority()
    test_gigachat_tokens()
    test_check_key()
    test_http()
    return finish("Проверка сессионных ключей облачных моделей")


if __name__ == "__main__":
    raise SystemExit(main())


