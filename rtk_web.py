"""
Точка входа ВЕБ-версии чата (агент, опрашивающий модели:
DeepSeek-flash и GigaChat).

Использует встроенный HTTP-сервер Python (стандартная библиотека),
приложение открывается в браузере по адресу http://127.0.0.1:8000.

Перед запуском печатается самодиагностика ключей моделей —
что не хватает и как исправить (файлы apidpsk.txt и gigakey.txt), но сервер
стартует в любом случае: доступные модели отвечают, а недоступные показывают
рекомендацию прямо в чате.

Запуск:
    python rtk_web.py            # сервер на 8000, автооткрытие страницы
    python rtk_web.py 9000       # сервер на другом порту
    python rtk_web.py 9000 0.0.0.0
"""
from rtk_app import key_check
from web import server


def main():
    # 1. Самодиагностика файлов/ключей моделей (с советами).
    # На VPS её можно отключить/облегчить:
    #   RTK_SKIP_KEY_CHECK=1 — совсем не проверять ключи;
    #   RTK_CHECK_NETWORK=0  — проверять файлы без сетевых запросов.
    from rtk_app import config
    if not getattr(config, "SKIP_KEY_CHECK", False):
        try:
            reports = key_check.check_all(
                network=getattr(config, "CHECK_NETWORK", True))
            key_check.print_report(reports, verbose=True)
        except Exception as exc:        # на всякий случай не роняем старт
            print("[ключи] не удалось выполнить проверку:", exc)
    else:
        print("[ключи] проверка отключена (RTK_SKIP_KEY_CHECK=1).")

    # 2. Запуск сервера (стартует всегда; неготовые модели покажут совет).
    server.main()


if __name__ == "__main__":
    main()
