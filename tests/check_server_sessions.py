# -*- coding: utf-8 -*-
"""Интеграционный тест HTTP-сервера с лимитом сессий (web/server.py).

Поднимает РЕАЛЬНЫЙ ThreadingHTTPServer с фейковым агентом (без сети) и
проверяет по HTTP:
  * выдачу cookie sid на GET / и изоляцию сессий;
  * заглушку 503 при переполнении (3-й клиент);
  * /api/health, /api/heartbeat и rate limit (429);
  * освобождение слота и /api/newslot.

Запуск:  python tests/check_server_sessions.py
"""
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
from rtk_app import config          # noqa: E402
from web import server as srv       # noqa: E402


class FakeAgent:
    label = "fake"

    def available(self):
        return [{"label": "fake"}]

    def answer(self, *a, **k):
        return {"ok": True, "text": "ok", "html": "<b>ok</b>",
                "answers": [{"label": "fake", "model": "fake", "text": "ok"}]}

    def answer_via_mcp(self, *a, **k):
        return {"ok": True, "text": "mcp", "html": "", "answers": []}


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
        with opener.open(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def main():
    ensure_utf8()
    tmp = tempfile.mkdtemp()
    config.SESSION_DIR = tmp
    config.MAX_SESSIONS = 2
    config.IDLE_TIMEOUT = 3600    # чтобы не мешало в тесте
    config.CHECK_TIMEOUT = 3600
    config.RATE_LIMIT_PER_MIN = 0  # rate limit проверяем отдельно

    from web import session_manager as sm
    mgr = sm.SessionManager(max_sessions=2, session_dir=tmp)
    mgr._gc_thread = None

    httpd = srv.create_server(FakeAgent(), host="127.0.0.1", port=0)
    srv._ServerState._RTK_VPS_SESSIONS = mgr   # подменяем реестр
    port = httpd.server_address[1]
    base = "http://127.0.0.1:%d" % port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        section("1. GET / выдаёт cookie sid")
        a = make_opener()
        st, body, hdrs = call(a, base + "/")
        check("страница отдана (200)", st == 200)
        set_cookie = hdrs.get("Set-Cookie", "")
        check("Set-Cookie содержит sid=", "sid=" in set_cookie)
        check("cookie HttpOnly", "HttpOnly" in set_cookie)

        b = make_opener()
        call(b, base + "/")

        section("2. /api/health")
        st, body, _ = call(a, base + "/api/health")
        j = json.loads(body)
        check("health ok", st == 200 and j.get("ok"))
        check("занято 2 сессии", j["sessions"]["active"] == 2)

        section("3. Третий клиент -> заглушка 503")
        c = make_opener()
        st, body, hdrs = call(c, base + "/")
        check("заглушка 503", st == 503)
        check("Retry-After присутствует", "Retry-After" in hdrs)
        check("текст про занятость", "занят" in body.lower())

        section("4. Заглушка и на /api/ask у третьего")
        st, body, _ = call(c, base + "/api/ask", "POST",
                           {"question": "привет", "models": [{"label": "fake"}]})
        check("ask -> 503 у третьего", st == 503)

        section("5. /api/ask первого работает")
        st, body, _ = call(a, base + "/api/ask", "POST",
                           {"question": "привет", "models": [{"label": "fake"}]})
        j = json.loads(body)
        check("ask ok", st == 200 and j.get("ok"))
        check("ответ непустой", j.get("text") == "ok")

        section("6. /api/heartbeat (тихий)")
        st, body, _ = call(a, base + "/api/heartbeat")
        j = json.loads(body)
        check("heartbeat ok", st == 200 and j.get("ok"))
        check("state активен", j["session"]["state"] == "active")

        section("7. Освобождение слота")
        first_sid = next(iter(mgr._sessions))
        mgr.release(first_sid)
        check("после release активных 1", mgr.info()["active"] == 1)
        st, body, _ = call(c, base + "/")
        check("третий вошёл (200)", st == 200)

        section("8. /api/newslot выдаёт новый sid")
        d = make_opener()
        call(d, base + "/")     # займём слот (если есть)
        st, body, hdrs = call(d, base + "/api/newslot", "POST", {})
        j = json.loads(body)
        check("newslot ответил (200 или 503)", st in (200, 503))
        if st == 200:
            check("newslot вернул sid", bool(j.get("sid")))

        section("9. Rate limit по IP -> 429")
        # Включаем лимит и шлём пачку запросов.
        from web import rate_limit
        config.RATE_LIMIT_PER_MIN = 3
        rate_limit._LIMITER = None   # новый лимитер с новым значением
        e = make_opener()
        codes = []
        retry = None
        for _ in range(6):
            st, body, hdrs = call(e, base + "/api/session")
            codes.append(st)
            if st == 429:
                retry = hdrs.get("Retry-After")
        check("часть запросов отклонена (429)", 429 in codes)
        check("есть Retry-After", retry is not None)
        config.RATE_LIMIT_PER_MIN = 0
        rate_limit._LIMITER = None
    finally:
        httpd.shutdown()

    return finish()


if __name__ == "__main__":
    sys.exit(main())
