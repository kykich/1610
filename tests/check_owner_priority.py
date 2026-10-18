# -*- coding: utf-8 -*-
"""Автономный тест ПРИОРИТЕТА ВЛАДЕЛЬЦА (web/session_manager.py).

Проверяет: при нехватке слотов привилегированный (владельческий) запрос
всегда получает место — вытесняется самая старая ОБЫЧНАЯ сессия. Обычные
запросы по-прежнему видят «нет свободной сессии» (full). Сеть не нужна.

Запуск:  python tests/check_owner_priority.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from web import session_manager as sm  # noqa: E402


def main():
    ensure_utf8()
    tmp = tempfile.mkdtemp()
    mgr = sm.SessionManager(max_sessions=2, session_dir=tmp)

    section("1. Заполняем оба слота обычными сессиями")
    k1, s1 = mgr.touch("sid-A")
    k2, s2 = mgr.touch("sid-B")
    check("A -> new", k1 == "new")
    check("B -> new", k2 == "new")
    check("активных 2", mgr.info()["active"] == 2)

    section("2. Третья ОБЫЧНАЯ сессия -> full (заглушка)")
    k3, s3 = mgr.touch("sid-C")
    check("C -> full", k3 == "full" and s3 is None)

    section("3. ВЛАДЕЛЕЦ входит при полном сервисе -> получает слот")
    # Делаем A «старее» B, чтобы вытеснилась именно A.
    s1.last_seen = s1.last_seen - 100
    kown, sown = mgr.touch("owner-sid", privileged=True)
    check("владелец -> new (не full)", kown == "new" and sown is not None)
    check("владелец помечен privileged", bool(sown and sown.privileged))
    check("активных снова 2", mgr.info()["active"] == 2)
    # Вытеснена самая старая ОБЫЧНАЯ сессия (A).
    k_a, _ = mgr.touch("sid-A")
    check("старая обычная сессия A вытеснена (offline)", k_a == "offline")
    # B на месте (не тронут).
    check("B остался активен", mgr.heartbeat("sid-B")["state"] == sm.ACTIVE)

    section("4. Второй владелец -> also new (вытесняет единственную обычную B)")
    kown2, sown2 = mgr.touch("owner-sid-2", privileged=True)
    check("второй владелец -> new", kown2 == "new" and sown2 is not None)
    check("B вытеснен", mgr.touch("sid-B")[0] == "offline")
    check("активных 2 (оба владельческих)", mgr.info()["active"] == 2)

    section("5. Владелец НЕ вытесняет владельца: обычному -> full")
    # Сейчас 2 владельческие сессии. Обычная третья должна получить full.
    kb, sb = mgr.touch("sid-ordinary")
    check("обычному при 2 владельцах -> full", kb == "full" and sb is None)

    section("6. Существующий владелец продлевается, флаг сохраняется")
    k, sess = mgr.touch("owner-sid", privileged=True)
    check("владелец -> ok", k == "ok")
    check("флаг privileged сохранён", bool(sess and sess.privileged))

    return finish()


if __name__ == "__main__":
    sys.exit(main())
