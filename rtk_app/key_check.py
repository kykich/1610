"""
Проверка учётных данных (ключей/файлов) для моделей агента.

Задача модуля — перед запуском помочь пользователю понять, чего не хватает
каждой модели:

* есть ли файл ключа;
* не пустой ли он;
* соответствует ли формат ожидаемому;
* действителен ли ключ (по желанию — реальным запросом по сети).

По итогам собираются советы (hint) для каждой модели: что создать/вставить,
чтобы модель заработала. Сам модуль ничего не отправляет в фоне сверх
последовательной проверки и не изменяет проект.
"""
import os

from . import config, deepseek, gigachat, runtime_keys

__all__ = ["STATUS_OK", "STATUS_MISSING", "STATUS_EMPTY", "STATUS_BAD_FORMAT",
           "STATUS_INVALID", "STATUS_SKIP", "ReportKey",
           "check_all", "check_key", "print_report"]

# --- Статусы проверки одной пары файл/модель ---
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_EMPTY = "empty"
STATUS_BAD_FORMAT = "bad_format"
STATUS_INVALID = "invalid"
STATUS_SKIP = "skip"

_STATUS_COLOR = {
    STATUS_OK: "[ OK ]",
    STATUS_MISSING: "[ N/A ]",
    STATUS_EMPTY: "[ EMPTY ]",
    STATUS_BAD_FORMAT: "[ FORMAT ]",
    STATUS_INVALID: "[ BAD ]",
    STATUS_SKIP: "[ ~ ]",
}


class ReportKey:
    """Результат проверки одной модели/файла ключа."""
    def __init__(self, label, file_path, status, detail, hint):
        self.label = label
        self.file_path = file_path
        self.status = status
        self.detail = detail
        self.hint = hint

    def ok(self):
        return self.status == STATUS_OK

    def to_dict(self):
        return {
            "model": self.label,
            "file": self.file_path,
            "ok": self.ok(),
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }

    def line(self, verbose=False):
        s = _STATUS_COLOR.get(self.status, "[?]") + " " + self.label
        if verbose:
            s += " — " + self.detail
        return s


# ---------------------------------------------------------------------------
# Чтение/проверка форматов
# ---------------------------------------------------------------------------
def _read_file(path):
    """None если файла нет, иначе .strip() содержимое ('' если пуст)."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def _deepseek_key_shape_ok(txt):
    """Грубая проверка формы ключа DeepSeek (вид 'sk-...')."""
    if not txt:
        return True  # обрабатывается отдельным статусом "empty"
    txt = txt.strip()
    if len(txt) < 28:
        return False
    if txt.startswith("sk-"):
        return True
    # нестандартный ключ — допускаем любой достаточно длинный без пробелов
    return " " not in txt and "\n" not in txt


def _is_base64_shape(txt):
    """True если похоже на base64 (длина кратна 4, только base64-символы)."""
    if not txt:
        return True
    import re as _re
    s = txt.strip().split(",")[-1].strip()
    if len(s) < 16 or len(s) % 4 != 0:
        return False
    return bool(_re.fullmatch(r"[A-Za-z0-9+/=]+", s))


# ---------------------------------------------------------------------------
# Глубокие проверки (по сети)
# ---------------------------------------------------------------------------
def _probe_deepseek_text(key):
    """Один запрос 'ping' к DeepSeek с ЯВНЫМ ключом. -> (ok:bool|None, label)."""
    key = str(key or "").strip()
    try:
        # allow_session=False: проверяем именно вставленный ключ, а не тот,
        # который мог прийти в этом же запросе из интерфейса.
        res = deepseek.chat(key, [{"role": "user", "content": "hi"}],
                            model=config.DS_MODELS[0], max_tokens=1,
                            allow_session=False)
        # Успешный HTTP-ответ означает, что ключ принят: контент может быть
        # пустым при max_tokens=1, поэтому наличие текста не требуем.
        content = res.get("content") if isinstance(res, dict) else ""
        return True, (content or "ответ получен")
    except Exception as exc:
        msg = str(exc)
        low = msg.lower()
        if any(t in low for t in ("401", "403", "unauthor", "authentication",
                                  "invalid", "forbidden")):
            return False, "ключ отвергнут сервером («%s»)" % msg[:100]
        # сетевые ошибки не означают невалидность -> None
        return None, "не удалось проверить по сети: %s" % msg[:120]


def _probe_deepseek(path):
    """Проверка ключа DeepSeek из файла. -> (ok:bool|None, label:str)"""
    return _probe_deepseek_text(_read_file(path) or "")


def _probe_gigachat_text(basic):
    """OAuth-токен GigaChat для ЯВНЫХ кредов. -> (ok|None, label)."""
    try:
        token = gigachat._get_access_token(basic=str(basic or "").strip(),
                                           allow_session=False)
        return (True, "OAuth-токен получен") if token else (False, "нет токена")
    except Exception as exc:
        msg = str(exc)
        low = msg.lower()
        if any(t in low for t in ("401", "403", "denied", "unauthor",
                                  "authentication", "invalid", "forbidden")):
            return False, "учётные данные не приняты Сбером («%s»)" % msg[:100]
        return None, "не удалось проверить OAuth-токен: %s" % msg[:120]


def _probe_gigachat_creds(path):
    """Проверка OAuth-кредов GigaChat из файла. -> (ok|None, label)"""
    return _probe_gigachat_text(_read_file(path) or "")


# ---------------------------------------------------------------------------
# Проверка ключа, ВСТАВЛЕННОГО пользователем (ничего не сохраняется)
# ---------------------------------------------------------------------------
def _key_result(provider, status, detail, hint, masked=""):
    return {"ok": status == STATUS_OK, "provider": provider,
            "status": status, "detail": detail, "hint": hint,
            "masked": masked}


def check_key(provider, text, network=True):
    """Проверяет ключ, введённый в интерфейсе (на диск не пишется).

    provider — "deepseek" | "gigachat";
    network=False — только формат (без запросов к API).
    Возвращает dict: {ok, provider, status, detail, hint, masked}.
    """
    provider = str(provider or "").strip().lower()
    if provider == "deepseek":
        try:
            key = runtime_keys.normalize_deepseek(text)
        except ValueError as exc:
            return _key_result(provider, STATUS_BAD_FORMAT, str(exc),
                               "Вставьте ключ DeepSeek одной строкой (sk-…).")
        if not key:
            return _key_result(provider, STATUS_EMPTY, "ключ не заполнен",
                               "Вставьте ключ DeepSeek (sk-…).")
        if not _deepseek_key_shape_ok(key):
            return _key_result(
                provider, STATUS_BAD_FORMAT, "непохоже на ключ DeepSeek",
                "Ключ DeepSeek начинается с 'sk-' и содержит не менее "
                "28 символов без пробелов.", runtime_keys.mask(key))
        if not network:
            return _key_result(provider, STATUS_SKIP,
                               "формат в порядке (проверка сети отключена)",
                               "", runtime_keys.mask(key))
        ok, info = _probe_deepseek_text(key)
        if ok:
            return _key_result(provider, STATUS_OK,
                               "ключ рабочий (пробный запрос прошёл)", "",
                               runtime_keys.mask(key))
        if ok is False:
            return _key_result(
                provider, STATUS_INVALID, info,
                "Ключ не активен / неверен. Проверьте в кабинете DeepSeek, "
                "что ключ жив и есть баланс.", runtime_keys.mask(key))
        return _key_result(provider, STATUS_SKIP, info,
                           "Если при чате DeepSeek отвечает 'Ошибка 401' — "
                           "ключ неверен или не активен.",
                           runtime_keys.mask(key))

    if provider == "gigachat":
        try:
            basic = runtime_keys.normalize_gigachat(text)
        except ValueError as exc:
            return _key_result(provider, STATUS_BAD_FORMAT, str(exc),
                               "Нужна base64-строка client_id:client_secret "
                               "или сама пара id:secret.")
        if not basic:
            return _key_result(provider, STATUS_EMPTY,
                               "учётные данные не заполнены",
                               "Вставьте base64 client_id:client_secret.")
        if not _is_base64_shape(basic):
            return _key_result(
                provider, STATUS_BAD_FORMAT, "непохоже на base64",
                "Нужна base64 от client_id:client_secret (без пробелов и "
                "переводов строк).", runtime_keys.mask(basic))
        if not network:
            return _key_result(provider, STATUS_SKIP,
                               "формат в порядке (проверка сети отключена)",
                               "", runtime_keys.mask(basic))
        ok, info = _probe_gigachat_text(basic)
        if ok:
            return _key_result(provider, STATUS_OK,
                               "учётные данные приняты (OAuth-токен получен)",
                               "", runtime_keys.mask(basic))
        if ok is False:
            return _key_result(
                provider, STATUS_INVALID, info,
                "Сбер не принял client_id/secret. Проверьте: активны ли они, "
                "верен ли формат, открыт ли доступ (белый список IP / "
                "рус. контур).", runtime_keys.mask(basic))
        return _key_result(provider, STATUS_SKIP, info,
                           "Сеть могла быть недоступна. Если при чате "
                           "GigaChat ошибка авторизации — пересоздайте ключ.",
                           runtime_keys.mask(basic))

    raise ValueError("provider: ожидается 'deepseek' или 'gigachat'")


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------
def check_all(network=True):
    """Собирает отчёты о готовности всех трёх моделей агента.

    network=True — дополнительно проверяем действительность ключа реальным
    коротким запросом по сети; False — только локально (файлы + формат).
    Возвращает список ReportKey в порядке моделей агента.
    """
    reports = []

    # --- DeepSeek (ключ apidpsk.txt) ---
    ds_path = config.DS_KEY_FILE
    ds_txt = _read_file(ds_path)
    if ds_txt is None:
        reports.append(ReportKey(
            "DeepSeek-flash", ds_path, STATUS_MISSING,
            "файл не найден",
            "Создайте файл apidpsk.txt в корне проекта и впишите ключ "
            "DeepSeek (sk-…). Получается в кабинете api.deepseek.com → API "
            "Keys."))
    elif not ds_txt:
        reports.append(ReportKey(
            "DeepSeek-flash", ds_path, STATUS_EMPTY,
            "файл пуст",
            "Откройте apidpsk.txt и вставьте туда API-ключ DeepSeek "
            "(sk-…), сохраните."))
    elif not _deepseek_key_shape_ok(ds_txt):
        reports.append(ReportKey(
            "DeepSeek-flash", ds_path, STATUS_BAD_FORMAT,
            "непохоже на ключ",
            "В apidpsk.txt должен лежать ключ вида 'sk-…' одной строкой без "
            "переводов строк. Похоже, там что-то другое — замените ключ."))
    else:
        if network:
            ok, info = _probe_deepseek(ds_path)
            if ok:
                reports.append(ReportKey(
                    "DeepSeek-flash", ds_path, STATUS_OK,
                    "ключ рабочий (пробный запрос прошёл)", ""))
            elif ok is False:
                reports.append(ReportKey(
                    "DeepSeek-flash", ds_path, STATUS_INVALID,
                    info,
                    "Ключ не активен / неверен. Проверьте в кабинете "
                    "DeepSeek, что ключ жив и есть баланс; замените его в "
                    "apidpsk.txt."))
            else:
                reports.append(ReportKey(
                    "DeepSeek-flash", ds_path, STATUS_SKIP,
                    info,
                    "Запустили чат: если модель отвечает 'Ошибка 401' — "
                    "замените ключ в apidpsk.txt."))
        else:
            reports.append(ReportKey(
                "DeepSeek-flash", ds_path, STATUS_SKIP,
                "файл непустой (проверка сети отключена)", ""))

    # --- GigaChat ---
    gc_path = config.GC_KEY_FILE
    gc_txt = _read_file(gc_path)
    if gc_txt is None:
        reports.append(ReportKey(
            "GigaChat", gc_path, STATUS_MISSING,
            "файл не найден",
            "Создайте gigakey.txt: внутри должна быть base64-строка "
            "client_id:client_secret. Получите клиентские данные в консоли "
            "SberCloud / GigaChat API (раздел 'API-ключ')."))
    elif not gc_txt:
        reports.append(ReportKey(
            "GigaChat", gc_path, STATUS_EMPTY,
            "файл пуст",
            "Впишите в gigakey.txt base64 от пары client_id:client_secret "
            "(без пробелов и переводов строк)."))
    elif not _is_base64_shape(gc_txt):
        reports.append(ReportKey(
            "GigaChat", gc_path, STATUS_BAD_FORMAT,
            "непохоже на base64",
            "Нужна base64 от client_id:client_secret. Как сделать:\n"
            "    python -c \"import base64;print(base64.b64encode("
            "b'ID:SECRET').decode())\"\n"
            "и результат вставьте в gigakey.txt."))
    else:
        if network:
            ok, info = _probe_gigachat_creds(gc_path)
            if ok:
                reports.append(ReportKey(
                    "GigaChat", gc_path, STATUS_OK,
                    "учётные данные приняты (OAuth-токен получен)", ""))
            elif ok is False:
                reports.append(ReportKey(
                    "GigaChat", gc_path, STATUS_INVALID, info,
                    "Сбер не принял client_id/secret. Проверьте: активны ли "
                    "они, верен ли формат, открыт ли доступ (белый список "
                    "IP / рус. контур)."))
            else:
                reports.append(ReportKey(
                    "GigaChat", gc_path, STATUS_SKIP, info,
                    "Сеть могла быть недоступна. Если при чате GigaChat "
                    "даёт ошибку авторизации — пересоздайте gigakey.txt."))
        else:
            reports.append(ReportKey(
                "GigaChat", gc_path, STATUS_SKIP,
                "файл непустой и похож на base64 (сеть отключена)", ""))

    return reports


# ---------------------------------------------------------------------------
# Вывод картинки при старте
# ---------------------------------------------------------------------------
def print_report(reports, verbose=True):
    all_ok = True
    for r in reports:
        tag = _STATUS_COLOR.get(r.status, "[?]")
        plain = "%s  %-30s" % (tag, r.label)
        print(plain)
        if r.status not in (STATUS_OK, STATUS_SKIP) or verbose:
            if r.detail:
                print("      деталь: " + r.detail)
            if r.hint:
                print("      совет : " + r.hint)
        if not r.ok():
            all_ok = False
        print()
    if all_ok:
        print("Готово: все модели доступны. Можно запускать чат.")
    else:
        print("Замечания выше — что мешает модели. После исправления")
        print("ключевых файлов перезапустите: python rtk_web.py .")
    return all_ok
