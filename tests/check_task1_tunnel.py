# -*- coding: utf-8 -*-
"""Автономная проверка Задачи 1 (сильная LLM с ПК через SSH-туннель).

Проверяем БЕЗ реальной сети и SSH:
  * ПК-агент сканирует папку и возвращает серверы/модели (структура ответа);
  * служебный HTTP-API агента отдаёт /health, /scan, /confirm_status,
    /confirm — через реальный ThreadingHTTPServer на loopback-порту 0;
  * ВПС-сторона (_tunnel_payload / _agent_api) собирает корректный статус
    движка и мягко деградирует, когда туннель недоступен.

Запуск:
    python tests/check_task1_tunnel.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8

import rtk_pc_agent.agent as pca


def _serve_agent(root):
    """Поднимает служебный API агента на свободном loopback-порту.

    Возвращает (httpd, port, state, thread).
    """
    args = pca.build_parser().parse_args(
        ["--vps", "rtk@test", "--root", root, "--agent-port", "0"])
    state = pca._ConfirmState()
    handler = pca.make_handler(args, state)
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd, port, state, th


def _get(port, path):
    with urllib.request.urlopen(
            "http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(port, path, obj):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _status(port, path):
    """Возвращает HTTP-код ответа агента (в т.ч. для 404)."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main():
    ensure_utf8()

    # ------------------------------------------------------------------
    section("1. Сканирование папки ПК (_scan_folder)")
    tmp = tempfile.mkdtemp(prefix="rtk_pc_scan_")
    # Создаём структуру: сервер + две модели + посторонний файл.
    with open(os.path.join(tmp, "llama-server.exe"), "wb") as f:
        f.write(b"MZ")
    with open(os.path.join(tmp, "Qwen-9B.gguf"), "wb") as f:
        f.write(b"\0" * 2048)
    sub = os.path.join(tmp, "sub")
    os.makedirs(sub)
    with open(os.path.join(sub, "Qwen-2B.gguf"), "wb") as f:
        f.write(b"\0" * 1024)
    with open(os.path.join(tmp, "readme.txt"), "wb") as f:
        f.write(b"x")

    sc = pca._scan_folder(tmp)
    check("скан вернул корень", sc.get("root") == os.path.abspath(tmp),
          "%r" % sc.get("root"))
    check("найден 1 сервер", len(sc["servers"]) == 1, "%r" % sc["servers"])
    check("сервер распознан как llama.cpp",
          sc["servers"][0]["kind"] == "llama.cpp")
    check("найдено 2 модели (рекурсивно)", len(sc["models"]) == 2,
          "%r" % sc["models"])
    names = sorted(m["name"] for m in sc["models"])
    check("имена моделей корректны",
          names == ["Qwen-2B.gguf", "Qwen-9B.gguf"], "%r" % names)
    check("размер модели посчитан (>0)",
          all(m["size_mb"] >= 0 for m in sc["models"]))

    bad = pca._scan_folder(os.path.join(tmp, "nope"))
    check("несуществующая папка -> error", "error" in bad, "%r" % bad)

    # ------------------------------------------------------------------
    section("2. Служебный HTTP-API агента (loopback)")
    httpd, port, state, th = _serve_agent(tmp)
    try:
        h = _get(port, "/health")
        check("/health отвечает ok", h.get("ok") is True, "%r" % h)

        cs = _get(port, "/confirm_status")
        check("/confirm_status: ещё не подтверждено",
              cs.get("confirmed") is False and cs.get("alive") is True,
              "%r" % cs)

        s1 = _get(port, "/scan")
        check("GET /scan использует root по умолчанию",
              len(s1.get("models") or []) == 2, "%r" % s1)

        s2 = _post(port, "/scan", {"root": tmp})
        check("POST /scan возвращает модели", len(s2.get("models") or []) == 2,
              "%r" % s2)

        c1 = _post(port, "/confirm", {"ok": True})
        check("POST /confirm подтверждает", c1.get("confirmed") is True,
              "%r" % c1)
        cs2 = _get(port, "/confirm_status")
        check("после confirm статус = подтверждено",
              cs2.get("confirmed") is True, "%r" % cs2)

        # Отказ.
        d1 = _post(port, "/confirm", {"ok": False})
        check("POST /confirm(ok=false) отклоняет",
              d1.get("confirmed") is False and d1.get("denied") is True,
              "%r" % d1)

        check("неизвестный путь -> 404",
              _status(port, "/nope") == 404)
    finally:
        httpd.shutdown()
        httpd.server_close()

    # ------------------------------------------------------------------
    section("3. Команда SSH-туннеля (_ssh_tunnel_cmd)")
    cmd = pca._ssh_tunnel_cmd("rtk@vps", 2222, 8080, 18080, 18081)
    joined = " ".join(cmd)
    check("используется -N", "-N" in cmd, joined)
    check("reverse-форвардинг движка есть",
          "127.0.0.1:18080:127.0.0.1:8080" in cmd, joined)
    check("reverse-форвардинг служебного API есть",
          "127.0.0.1:18081:127.0.0.1:18081" in cmd, joined)
    check("задан нестандартный SSH-порт", "-p" in cmd and "2222" in cmd,
          joined)
    check("ExitOnForwardFailure включён",
          "ExitOnForwardFailure=yes" in joined, joined)
    check("адрес VPS — последний аргумент", cmd[-1] == "rtk@vps", joined)

    # ------------------------------------------------------------------
    section("4. ВПС-сторона: статус движка и деградация без туннеля")
    from rtk_pc_agent.agent import _ConfirmState  # noqa
    import web.server as srv
    from rtk_app import config

    # Без реального туннеля probe вернёт alive=False — payload не должен падать.
    payload = srv._local_status_payload()
    check("_local_status_payload содержит engine",
          "engine" in payload, "%r" % list(payload.keys()))
    check("engine по умолчанию — weak или strong",
          payload.get("engine") in ("weak", "strong"),
          "%r" % payload.get("engine"))
    check("метки моделей присутствуют",
          payload.get("weak_label") == config.WEAK_LABEL and
          payload.get("strong_label") == config.STRONG_LABEL, "%r" % payload)
    tun = payload.get("tunnel") or {}
    check("порт туннеля в payload",
          tun.get("tunnel_port") == config.TUNNEL_PORT, "%r" % tun)
    check("порт агента в payload",
          tun.get("agent_port") == config.AGENT_PORT, "%r" % tun)
    check("вложенный tunnel содержит pc_alive",
          "pc_alive" in tun, "%r" % tun)

    tp = srv._tunnel_payload()
    check("_tunnel_payload возвращает dict", isinstance(tp, dict), "%r" % tp)
    check("_tunnel_payload содержит pc_alive", "pc_alive" in tp, "%r" % tp)
    check("_tunnel_payload содержит engine", "engine" in tp, "%r" % tp)

    # _agent_api к заведомо недоступному порту -> мягкий отказ (без исключения).
    real_port = config.AGENT_PORT
    config.AGENT_PORT = 1  # заведомо закрытый порт
    try:
        res = srv._agent_api("/health", timeout=1)
        check("_agent_api без туннеля -> ok=False (degrade)",
              res.get("ok") is False, "%r" % res)
    finally:
        config.AGENT_PORT = real_port

    # ------------------------------------------------------------------
    section('5. "Вариант А": команда запуска ПК-агента (_pair_payload)')
    pair = srv._pair_payload()
    check("_pair_payload возвращает dict", isinstance(pair, dict),
          "%r" % pair)
    for key in ("vps", "engine_port", "tunnel_port", "agent_port",
                "identity", "command", "script"):
        check("_pair_payload содержит ключ %r" % key, key in pair,
              "%r" % list(pair.keys()))
    check("engine_port совпадает с config.LOCAL_PORT",
          pair["engine_port"] == config.LOCAL_PORT, "%r" % pair["engine_port"])
    check("tunnel_port совпадает с config.TUNNEL_PORT",
          pair["tunnel_port"] == config.TUNNEL_PORT, "%r" % pair["tunnel_port"])
    check("agent_port совпадает с config.AGENT_PORT",
          pair["agent_port"] == config.AGENT_PORT, "%r" % pair["agent_port"])
    check("identity указывает на ключ rtk_pc",
          "rtk_pc" in pair["identity"], "%r" % pair["identity"])

    # command — однострочная PowerShell-команда, script — многострочный .ps1.
    check("command — модуль rtk_pc_agent",
          "rtk_pc_agent" in pair["command"], "%r" % pair["command"])
    check("command содержит порт движка",
          str(config.LOCAL_PORT) in pair["command"], "%r" % pair["command"])
    check("command содержит порт туннеля",
          str(config.TUNNEL_PORT) in pair["command"], "%r" % pair["command"])
    check("command содержит порт агента",
          str(config.AGENT_PORT) in pair["command"], "%r" % pair["command"])
    check("command — одна строка (без переносов)",
          "\n" not in pair["command"] and "\r" not in pair["command"],
          "%r" % pair["command"])
    check("script — многострочный (есть переносы)",
          "\n" in pair["script"] or "\r" in pair["script"])
    check("script содержит тот же модуль",
          "rtk_pc_agent" in pair["script"], "%r" % pair["script"])

    # Пустой TUNNEL_SSH -> плейсхолдер вместо реального адреса.
    real_ssh = config.TUNNEL_SSH
    try:
        config.TUNNEL_SSH = ""
        p_empty = srv._pair_payload()
        check("при пустом TUNNEL_SSH поле vps пусто",
              p_empty["vps"] == "", "%r" % p_empty["vps"])
        check("при пустом TUNNEL_SSH есть плейсхолдер <IP-этого-VPS>",
              "IP-этого-VPS" in p_empty["command"],
              "%r" % p_empty["command"])

        config.TUNNEL_SSH = "rtk@10.0.0.5"
        p_full = srv._pair_payload()
        check("при заданном TUNNEL_SSH поле vps заполнено",
              p_full["vps"] == "rtk@10.0.0.5", "%r" % p_full["vps"])
        check("при заданном TUNNEL_SSH адрес подставлен в command",
              "rtk@10.0.0.5" in p_full["command"],
              "%r" % p_full["command"])
        check("при заданном TUNNEL_SSH плейсхолдера нет",
              "IP-этого-VPS" not in p_full["command"],
              "%r" % p_full["command"])
    finally:
        config.TUNNEL_SSH = real_ssh

    # Убираем временную папку.
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    sys.exit(finish())


if __name__ == "__main__":
    main()
