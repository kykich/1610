# -*- coding: utf-8 -*-
"""Автономный тест реестра клиентских сессий (web/session_manager.py).

Проверяет §5 ТЗ: лимит активных сессий, изоляцию данных, изоляцию sid,
автомат ACTIVE -> CHECK -> OFFLINE, освобождение слота и read-only после
отключения. Сеть не используется.

Запуск:  python tests/check_sessions.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import config          # noqa: E402
from web import session_manager as sm  # noqa: E402


def main():
    ensure_utf8()
    tmp = tempfile.mkdtemp()
    # Короткие таймауты для быстрого теста.
    config.IDLE_TIMEOUT = 1      # 1 с тишины -> CHECK
    config.CHECK_TIMEOUT = 1     # 1 с без ответа -> OFFLINE
    mgr = sm.SessionManager(max_sessions=2, session_dir=tmp)

    section("1. Лимит активных сессий")
    k1, s1 = mgr.touch("sid-A")
    check("первая сессия -> new", k1 == "new" and s1 is not None)
    k2, s2 = mgr.touch("sid-B")
    check("вторая сессия -> new", k2 == "new")
    k3, s3 = mgr.touch("sid-C")
    check("третья сессия -> full (заглушка)", k3 == "full" and s3 is None)
    check("активных ровно 2", mgr.info()["active"] == 2)

    section("2. Изоляция данных сессий")
    s1.store.append_turn("вопрос A", {"role": "assistant", "content": "ответ A"})
    s2.store.append_turn("вопрос B", {"role": "assistant", "content": "ответ B"})
    snap_a = s1.store.snapshot()
    snap_b = s2.store.snapshot()
    check("история A не пуста", len(snap_a) >= 2)
    check("у A есть 'вопрос A'", any("вопрос A" in str(m) for m in snap_a))
    check("у B НЕТ 'вопрос A'", not any("вопрос A" in str(m) for m in snap_b))
    check("файлы сессий разные", s1.store.path != s2.store.path)
    check("файл сессии A существует", os.path.isfile(s1.store.path))

    section("3. Автомат ACTIVE -> CHECK по тишине")
    time.sleep(1.2)
    hb_a = mgr.heartbeat("sid-A")
    check("A перешёл в CHECK", hb_a["state"] == sm.CHECK)
    check("у CHECK есть deadline", hb_a["deadline"] is not None)

    section("4. Реальный запрос возвращает CHECK -> ACTIVE")
    k, s = mgr.touch("sid-A")
    check("запрос -> ok", k == "ok")
    check("состояние снова ACTIVE", s.state == sm.ACTIVE)
    check("активных всё ещё 2", mgr.info()["active"] == 2)

    section("5. CHECK -> OFFLINE без ответа, слот освобождается")
    mgr.touch("sid-B")                  # B: ACTIVE (продлеваем заранее)
    time.sleep(1.2)
    mgr.gc()                            # A -> CHECK
    check("после gc A в CHECK",
          mgr.heartbeat("sid-A")["state"] == sm.CHECK)
    mgr.touch("sid-B")                  # вернуть B в ACTIVE до его OFFLINE
    time.sleep(1.2)
    mgr.gc()                            # CHECK(A) -> OFFLINE
    mgr.touch("sid-B")
    hb_a2 = mgr.heartbeat("sid-A")
    check("A теперь OFFLINE", hb_a2["state"] == sm.OFFLINE)
    check("активных стало 1", mgr.info()["active"] == 1)

    section("6. Освободился слот -> 3-й клиент может войти")
    k4, s4 = mgr.touch("sid-C")
    check("C теперь получает сессию", k4 == "new")
    check("активных снова 2", mgr.info()["active"] == 2)

    section("7. Отключённый sid возвращает offline (read-only)")
    k_old, s_old = mgr.touch("sid-A")
    check("старый sid -> offline (не новая сессия)", k_old == "offline")

    section("8. 'Начать новую сессию' взамен отключённой")
    mgr.release("sid-B")
    check("после release активных 1", mgr.info()["active"] == 1)
    kind, new_sid = mgr.new_slot(old_sid="sid-A")
    check("new_slot выдал новую сессию", kind == "new" and new_sid)
    check("новый sid отличается от старого", new_sid != "sid-A")
    check("активных 2", mgr.info()["active"] == 2)

    section("9. Заглушка при переполнении через new_slot")
    kind2, _ = mgr.new_slot(old_sid="sid-ZZZ")
    check("new_slot -> full при 2 активных", kind2 == "full")

    section("10. touch(None) генерирует новый sid")
    tmp2 = tempfile.mkdtemp()
    mgr2 = sm.SessionManager(max_sessions=2, session_dir=tmp2)
    kind, sess = mgr2.touch(None)
    check("touch(None) -> new", kind == "new")
    check("sid сгенерирован", bool(sess and sess.sid))

    return finish()


if __name__ == "__main__":
    sys.exit(main())
