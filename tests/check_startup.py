# -*- coding: utf-8 -*-
"""Проверка политики запуска моделей (этап «только пользователем»).

Проверяется БЕЗ запуска моделей и сети:
  1. При старте веб-сервера активна ТОЛЬКО слабая модель VPS;
  2. Восстановление туннеля НЕ переключает на сильную модель автоматически
     (если AUTO_STRONG выключен), обрыв — безопасно возвращает на слабую;
  3. Запуск модели требует явного выбора после сканирования (helper
     _model_selection_error), без подстановки «первой попавшейся»;
  4. Для RAG-модели действует та же проверка по именам из скана.

Запуск: python -m tests.check_startup
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import config  # noqa: E402
import web.server as web_server  # noqa: E402


def test_initial_engine():
    section("1. Старт сервера: только слабая модель")
    check("AUTO_STRONG по умолчанию выключен",
          getattr(config, "AUTO_STRONG", True) is False)
    check("_initial_engine() == weak",
          web_server._initial_engine() == "weak")


def test_tunnel_policy():
    section("2. Фоновый контроль туннеля без авто-перехода на сильную")
    check("weak + туннель жив -> weak",
          web_server._next_engine_on_tick(True, "weak") == "weak")
    check("weak + туннель мёртв -> weak",
          web_server._next_engine_on_tick(False, "weak") == "weak")
    check("strong + туннель жив -> strong",
          web_server._next_engine_on_tick(True, "strong") == "strong")
    check("strong + туннель оборван -> weak",
          web_server._next_engine_on_tick(False, "strong") == "weak")

    old = getattr(config, "AUTO_STRONG", False)
    config.AUTO_STRONG = True
    try:
        check("AUTO_STRONG=True: weak + туннель жив -> strong",
              web_server._next_engine_on_tick(True, "weak") == "strong")
    finally:
        config.AUTO_STRONG = old


def test_model_selection():
    section("3. Запуск только после сканирования и выбора модели")
    err = web_server._model_selection_error("", {})
    check("без модели — ошибка", bool(err) and "просканируйте" in err.lower(),
          err)
    err = web_server._model_selection_error("e:/models/a.gguf", {})
    check("без результатов скана — ошибка",
          bool(err) and "сканирования" in err.lower(), err)

    scan = {"models": [
        {"path": "e:/models/a.gguf", "name": "a.gguf"},
        {"path": "e:/models/b.gguf", "name": "b.gguf"},
    ]}
    check("модель из скана — без ошибки",
          web_server._model_selection_error("e:/models/a.gguf", scan) == "")
    err = web_server._model_selection_error("e:/models/c.gguf", scan)
    check("модель не из скана — ошибка",
          bool(err) and "отсутствует" in err.lower(), err)

    # RAG-скан: выбор по имени модели.
    embed_scan = {"models": [
        {"name": "nomic-embed-text-v2-moe:latest"},
        {"name": "bge-m3:latest"},
    ]}
    check("RAG-модель из скана — без ошибки",
          web_server._model_selection_error(
              "nomic-embed-text-v2-moe:latest", embed_scan,
              field="name") == "")
    err = web_server._model_selection_error("other:latest", embed_scan,
                                            field="name")
    check("RAG-модель не из скана — ошибка",
          bool(err) and "отсутствует" in err.lower(), err)

    # /api/embed start без скана -> 400 (а не автозапуск первой модели).
    class _Handler(object):
        @staticmethod
        def _send_json(code, payload):
            return code, payload

    old_vps = web_server._ServerState.embed_scan
    old_pc = web_server._ServerState.pc_embed_scan
    web_server._ServerState.embed_scan = None
    web_server._ServerState.pc_embed_scan = None
    try:
        code, payload = web_server.WebRequestHandler._embed_start(
            _Handler(), {"model": "nomic-embed-text-v2-moe:latest"}, pc=True)
    finally:
        web_server._ServerState.embed_scan = old_vps
        web_server._ServerState.pc_embed_scan = old_pc
    check("embed start без скана -> 400", code == 400, str(payload))
    check("embed start требует скан", bool(payload.get("scan_required")))


def main():
    ensure_utf8()
    test_initial_engine()
    test_tunnel_policy()
    test_model_selection()
    return finish("Проверка политики запуска моделей")


if __name__ == "__main__":
    raise SystemExit(main())
