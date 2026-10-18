# -*- coding: utf-8 -*-
"""Реестр клиентских сессий с автоматом состояний и контрольным пингом.

Реализует §5 ТЗ (docs/vps-llm-service.md):

  * НЕ БОЛЕЕ ``MAX_SESSIONS`` (по умолчанию 2) АКТИВНЫХ сессий. 3-й и далее
    подключившийся получает заглушку «нет свободной сессии» (503).
  * У каждого браузера — СВОЯ сессия (по cookie ``sid``) со своим
    изолированным ``SessionStore`` (файл ``session/<sid>.json``).
  * Автомат жизненного цикла:

        ACTIVE --(idle > IDLE_TIMEOUT)--> CHECK
        CHECK  --(нет ответа > CHECK_TIMEOUT)--> OFFLINE (слот освобождён)
        ACTIVE/CHECK --(запрос)--> ACTIVE (last_seen обновлён)

  * Решение о переходе принимает СЕРВЕР (фоновый сборщик + проверка при
    запросе). Браузер лишь ОПРАШИВАЕТ состояние через ``GET /api/heartbeat``
    и показывает диалог «Вы здесь?» при ``state == "check"`` (§5.4).
  * Cookie ``sid`` НЕ привязан к IP: смена IP не создаёт новую сессию.

Модуль не зависит от HTTP-слоя — его можно тестировать отдельно.
"""
import os
import secrets
import threading
import time

from rtk_app import config
from rtk_app.session_store import SessionStore

# --- Состояния сессии ---
ACTIVE = "active"      # обслуживается, слот занят
CHECK = "check"        # «на проверке»: контрольный пинг, ждём ответа
OFFLINE = "offline"    # отключена: слот свободен, страница read-only


class Session:
    """Одна клиентская сессия: стор + состояние + таймстемпы."""

    def __init__(self, sid, store, privileged=False):
        self.sid = sid
        self.store = store
        self.state = ACTIVE
        self.last_seen = time.time()      # последняя РЕАЛЬНАЯ активность
        self.check_at = None              # когда перешли в CHECK (дедлайн = +CHECK_TIMEOUT)
        self.created = time.time()
        # Владелец (приоритетный слот): такую сессию НЕ вытесняем, и ей
        # всегда находится место, даже когда лимит исчерпан (см. touch()).
        self.privileged = bool(privileged)

    def deadline(self):
        """Момент дедлайна в CHECK (epoch) или None."""
        if self.state == CHECK and self.check_at is not None:
            return self.check_at + config.CHECK_TIMEOUT
        return None

    def snapshot(self):
        """Краткое состояние для отдачи клиенту."""
        return {
            "state": self.state,
            "deadline": self.deadline(),
            "idle_timeout": config.IDLE_TIMEOUT,
            "check_timeout": config.CHECK_TIMEOUT,
        }


class SessionManager:
    """Потокобезопасный реестр сессий с лимитом активных.

    Слоты считаются по числу сессий НЕ в OFFLINE (т.е. ACTIVE + CHECK).
    """

    def __init__(self, max_sessions=None, session_dir=None):
        self.max_sessions = int(max_sessions or config.MAX_SESSIONS)
        self.session_dir = session_dir or config.SESSION_DIR
        self._lock = threading.RLock()
        self._sessions = {}               # sid -> Session
        # Держим «память о прошлых sid», чтобы после OFFLINE клиент с тем же
        # cookie знал, что его сессия завершена (read-only), а не создавал
        # новую автоматически. sid -> время завершения.
        self._offline_seen = {}

    # ---------------- Служебное ----------------

    def _store_path(self, sid):
        os.makedirs(self.session_dir, exist_ok=True)
        # sid генерируется у нас (token_urlsafe), поэтому безопасен для имени,
        # но на всякий случай чистим недопустимые символы.
        safe = "".join(ch for ch in sid if ch.isalnum() or ch in "-_")[:64]
        return os.path.join(self.session_dir, "s_%s.json" % safe)

    def _new_session_locked(self, sid, privileged=False):
        """Создаёт сессию и её стор (вызывать под self._lock)."""
        store = SessionStore(
            path=self._store_path(sid),
            shared_rag=bool(getattr(config, "SHARED_RAG", False)))
        s = Session(sid, store, privileged=privileged)
        self._sessions[sid] = s
        return s

    def _active_count_locked(self):
        return sum(1 for s in self._sessions.values() if s.state != OFFLINE)

    def _evict_one_locked(self, now):
        """Вытесняет ОДНУ НЕпривилегированную сессию (самую старую).

        Используется для приоритетного слота владельца: если мест нет, но
        запрос привилегированный — освобождаем слот, переводя в OFFLINE
        самую давно неактивную обычную сессию. Владельческие сессии не
        трогаем. Возвращает True, если слот освобождён.
        """
        victims = [s for s in self._sessions.values()
                   if s.state != OFFLINE and not s.privileged]
        if not victims:
            return False
        # Самая старая по последней активности (last_seen).
        victim = min(victims, key=lambda s: s.last_seen)
        self._offline_seen[victim.sid] = now
        del self._sessions[victim.sid]
        print("[SESSION] вытеснена обычная сессия %s ради владельца, "
              "слот свободен" % victim.sid, flush=True)
        return True

    # ---------------- Публичный API ----------------

    def touch(self, sid, is_activity=True, privileged=False):
        """Точка входа при запросе с cookie ``sid``.

        Возвращает (kind, session):
          * kind == "ok"      — сессия существует и её можно обслуживать;
          * kind == "new"     — создана НОВАЯ сессия (слот был свободен);
          * kind == "full"    — свободных слотов нет (нужна заглушка 503);
          * kind == "offline" — сессия была отключена (read-only/новая).

        is_activity=False — «тихий» запрос (например /api/heartbeat): НЕ
        продлевает сессию и НЕ переводит её из CHECK, но возвращает состояние.

        privileged=True — запрос от ВЛАДЕЛЬЦА (приоритетный): если мест нет,
        вытесняется одна обычная сессия, чтобы владельцу всегда нашёлся слот.
        Владельцу, чья прежняя сессия ушла в OFFLINE, сразу выдаётся НОВАЯ
        привилегированная сессия (а не «offline»-экран) — чтобы он никогда не
        упирался в лимит.
        """
        now = time.time()
        with self._lock:
            self._gc_locked(now)
            s = self._sessions.get(sid) if sid else None
            if s is not None:
                if privileged:
                    s.privileged = True
                if is_activity and s.state in (ACTIVE, CHECK):
                    # Реальный запрос продлевает: CHECK/ACTIVE -> ACTIVE.
                    s.state = ACTIVE
                    s.check_at = None
                    s.last_seen = now
                return ("ok", s)
            # Сессии нет. Была отключена ранее?
            if sid and sid in self._offline_seen:
                # ВЛАДЕЛЬЦУ offline не показываем: сразу выдаём новый
                # привилегированный слот (освобождая память о старом sid).
                if not privileged:
                    return ("offline", None)
                self._offline_seen.pop(sid, None)
            # Новый клиент: есть ли слот?
            if self._active_count_locked() >= self.max_sessions:
                # Владельцу всегда освобождаем слот (вытесняем обычную сессию).
                if privileged and self._evict_one_locked(now):
                    pass
                else:
                    return ("full", None)
            # Если клиент пришёл с конкретным sid (cookie) — используем его;
            # иначе генерируем новый.
            new_sid = sid or self._gen_sid_locked()
            if new_sid in self._sessions:
                new_sid = self._gen_sid_locked()
            s = self._new_session_locked(new_sid, privileged=privileged)
            s.last_seen = now
            return ("new", s)

    def store_for(self, sid):
        """Возвращает SessionStore для sid (или None) — для привязки запроса
        после ротации слота (new_slot)."""
        with self._lock:
            s = self._sessions.get(sid) if sid else None
            return s.store if s is not None else None

    def heartbeat(self, sid):
        """Состояние сессии для клиента (без продления).

        Возвращает dict-снимок; для неизвестного/отключённого sid — state
        "offline" (клиент переходит в read-only).
        """
        now = time.time()
        with self._lock:
            self._gc_locked(now)
            s = self._sessions.get(sid)
            if s is None:
                return {"state": OFFLINE, "deadline": None,
                        "idle_timeout": config.IDLE_TIMEOUT,
                        "check_timeout": config.CHECK_TIMEOUT}
            return s.snapshot()

    def new_slot(self, old_sid=None, privileged=False):
        """Выдаёт НОВУЮ сессию взамен старой/отключённой.

        Используется кнопкой «Начать новую сессию» и обновлением страницы
        (перейти на новый cookie sid, освободив прежний слот).

        old_sid — текущий sid клиента: если задан, СЛОТ ОСВОБОЖДАЕТСЯ (сессия
        удаляется, чтобы обновление страницы не плодило «мёртвые» слоты).
        privileged — запрос от ВЛАДЕЛЬЦА: если мест нет, вытесняется самая
        старая ОБЫЧНАЯ сессия, чтобы владельцу всегда нашёлся слот.

        Возвращает (kind, sid): "new" — выдан новый sid; "full" — мест нет.
        """
        with self._lock:
            # ОСВОБОЖДАЕМ старый слот клиента (обновление страницы / смена sid).
            if old_sid:
                self._offline_seen.pop(old_sid, None)
                self._sessions.pop(old_sid, None)
            if self._active_count_locked() >= self.max_sessions:
                # Владельцу всегда находим место (вытесняем обычную сессию).
                if privileged and self._evict_one_locked(time.time()):
                    pass
                else:
                    if old_sid:
                        self._offline_seen[old_sid] = time.time()
                    return ("full", None)
            sid = self._gen_sid_locked()
            self._new_session_locked(sid, privileged=privileged)
            return ("new", sid)

    def release(self, sid):
        """Явно освобождает сессию (пользователь нажал «Завершить»)."""
        with self._lock:
            s = self._sessions.pop(sid, None)
            if s is not None:
                self._offline_seen[sid] = time.time()

    # ---------------- Фоновый сборщик ----------------

    def gc(self):
        """Разово продвигает состояния по таймаутам (вызывается сборщиком)."""
        with self._lock:
            self._gc_locked(time.time())

    def _gc_locked(self, now):
        idle_to = config.IDLE_TIMEOUT
        check_to = config.CHECK_TIMEOUT
        for sid, s in list(self._sessions.items()):
            if s.state == ACTIVE and (now - s.last_seen) >= idle_to:
                s.state = CHECK
                s.check_at = now
                print("[SESSION] %s -> CHECK (тишина %.0f c)"
                      % (sid, now - s.last_seen), flush=True)
            elif s.state == CHECK and s.check_at is not None and \
                    (now - s.check_at) >= check_to:
                # Ответа нет — отключаем, слот освобождается.
                s.state = OFFLINE
                self._offline_seen[sid] = now
                del self._sessions[sid]
                print("[SESSION] %s -> OFFLINE (нет ответа %.0f c), слот свободен"
                      % (sid, now - s.check_at), flush=True)

        # Чистим «память об offline» старше суток.
        cutoff = now - 86400
        for sid, ts in list(self._offline_seen.items()):
            if ts < cutoff:
                del self._offline_seen[sid]

    def info(self):
        """Сводка для /api/health."""
        with self._lock:
            return {
                "active": self._active_count_locked(),
                "max": self.max_sessions,
                "total": len(self._sessions),
            }

    # ---------------- sid ----------------

    @staticmethod
    def new_sid():
        return secrets.token_urlsafe(24)

    def _gen_sid_locked(self):
        sid = self.new_sid()
        while sid in self._sessions:
            sid = self.new_sid()
        return sid


# Синглтон реестра (лениво), чтобы HTTP-слой и фоновый сборщик использовали
# ОДИН и тот же объект.
_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def get_manager():
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = SessionManager()
    return _MANAGER


def start_gc_thread(manager=None, period=None):
    """Запускает фоновый поток-сборщик (идемпотентно)."""
    manager = manager or get_manager()
    period = int(period or getattr(config, "SESSION_GC_PERIOD", 30))
    th = getattr(manager, "_gc_thread", None)
    if th and th.is_alive():
        return th
    stop = threading.Event()
    manager._gc_stop = stop

    def _loop():
        while not stop.is_set():
            if stop.wait(period):
                break
            try:
                manager.gc()
            except Exception as exc:            # noqa: BLE001
                print("[SESSION] gc: %s" % exc, flush=True)

    th = threading.Thread(target=_loop, daemon=True, name="session-gc")
    manager._gc_thread = th
    th.start()
    return th
