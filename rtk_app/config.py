"""
Настройки проекта: эндпоинты и модели DeepSeek + GigaChat.
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- DeepSeek ---
DS_API_URL = "https://api.deepseek.com/chat/completions"
DS_KEY_FILE = os.path.join(BASE_DIR, "apidpsk.txt")
# Модель DeepSeek, которая даёт голос. Имя модели в API — deepseek-flash.
DS_MODELS = [
    "deepseek-flash",
]

# --- GigaChat (облачный Сбер) ---
# Ключ: base64 строка "client_id:client_secret" (без переводов).
GC_KEY_FILE = os.path.join(BASE_DIR, "gigakey.txt")
GC_TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GC_API_BASE = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
GC_SCOPE = "GIGACHAT_API_PERS"
GC_MODEL = "GigaChat"          # самая простая/лёгкая
GC_TOKEN_TTL = 1500            # секунд жизни access token (меньше серверного срока)

# --- Общее ---
REQUEST_TIMEOUT = 90

WEB_HOST = "127.0.0.1"
WEB_PORT = 8000
WEB_ROOT = BASE_DIR

# --- Alias / метки ---
KEY_FILE = DS_KEY_FILE
API_URL = DS_API_URL
MODEL = "DeepSeek-flash | GigaChat"

# --- Параметры генерации по умолчанию ---
# Температура, если пользователь не указал свою для модели
DEFAULT_TEMPERATURE = 0.7
# Нижняя/верхняя границы температуры в интерфейсе выбора модели
TEMP_MIN = 0.0
TEMP_MAX = 1.0
TEMP_STEP = 0.05

# --- Top-p (nucleus sampling): параметр разнообразия генерации ---
# Top-p — вероятность, в пределах которой суммируются токены-кандидаты.
# 1.0 — выключено (учитываются все токены). Типичное значение 0.9–0.95.
DEFAULT_TOP_P = 1.0
TOP_P_MIN = 0.0
TOP_P_MAX = 1.0
TOP_P_STEP = 0.05

# --- Repetition penalty (штраф за повторы) ---
# 1.0 — без штрафа; >1.0 — снижает дословные повторы. Типично 1.0–1.3.
DEFAULT_REP_PENALTY = 1.0
REP_PENALTY_MIN = 0.0
REP_PENALTY_MAX = 2.0
REP_PENALTY_STEP = 0.05

# --- Размер КОНТЕКСТНОГО ОКНА (context window, токенов) ---
# Сколько последних токенов истории модель «видит». 0 — не ограничивать
# (брать значение модели по умолчанию). В первую очередь актуально для
# локальных моделей (llama.cpp: -c/--ctx-size), но доступно и для облачных.
DEFAULT_CONTEXT_WINDOW = 0       # 0 — не ограничивать (авто)
CONTEXT_WINDOW_MIN = 0
CONTEXT_WINDOW_MAX = 131072
CONTEXT_WINDOW_STEP = 512

# --- Максимальное число новых токенов (max_tokens), глобальная настройка ---
# Используется только если пользователь включил параметр в интерфейсе.
# Если настройка выключена — сервер не передаёт max_tokens и модель сама
# выбирает разумный предел вывода.
DEFAULT_MAX_TOKENS = 2048       # значение по умолчанию в поле ввода
MAX_TOKENS_MIN = 1              # минимальное допустимое значение
MAX_TOKENS_MAX = 100000         # верхняя граница (про запас; API может урезать)
MAX_TOKENS_STEP = 50            # шаг изменения в интерфейсе

# --- Сессии диалога (история сохраняется на диск) ---
SESSION_DIR = os.path.join(BASE_DIR, "session")
SESSION_FILE = os.path.join(SESSION_DIR, "session.json")

# --- Сжатие истории диалога ---
# Сколько последних сообщений хранить/передавать полностью (остальное — summary).
# ВАЖНО: keep = 0 означает «сжатие не применять» — ни подстановка summary,
# ни автосжатие не выполняются, в запрос уходит полная история.
# По умолчанию 0 — сжатие выключено (используются стратегии из STRATEGY).
COMPACT_KEEP = 0

# --- Стратегии управления контекстом ---
# Взаимоисключающие стратегии (переключаются в интерфейсе):
#   "none"    — без стратегии: в запрос уходит полная история;
#   "sliding" — Sliding Window: только последние N сообщений, старое отбрасывается;
#   "facts"   — Sticky Facts / Key-Value Memory: блок facts + последние N сообщений;
#   "branch"  — Branching: независимые ветки диалога от checkpoint.
# Дополнительно к любой стратегии может работать сжатие (summary, COMPACT_*).
STRATEGY = "none"
# Сколько последних сообщений (окно) использовать стратегиям Sliding/Facts
# по умолчанию. При 0 — окно не ограничивается (вся история).
STRATEGY_WINDOW = 10
# Сколько веток создавать по умолчанию в стратегии Branch.
BRANCH_DEFAULT_COUNT = 2
# Сжатие истории выполняется АВТОМАТИЧЕСКИ и ИНКРЕМЕНТАЛЬНО (без кнопки):
# как только появляется ВЫТЕСНЕННОЕ сообщение (история стала больше keep),
# оно дописывается в summary. Т.е. для keep = 5 сжатие начинается с 6-го
# сообщения и продолжается по мере дальнейшего вытеснения (7-е, 8-е, …).
COMPACT_ENABLED = True
# Минимум сообщений в диалоге, при котором генерация summary имеет смысл.
COMPACT_MIN = 4
# Максимальная длина одного сообщения при подаче на сжатие (символов),
# чтобы запрос к модели не раздувался.
COMPACT_MSG_CAP = 2000
# Модель для генерации summary (используется GigaChat)
COMPACT_MODEL = "GigaChat"

# --- Модель памяти агента (три независимых типа) ---
# Память разделена на ТРИ хранимых ОТДЕЛЬНО типа:
#   "short"    — краткосрочная: текущий диалог (сообщения сессии/ветки);
#   "working"  — рабочая: данные текущей задачи (активная задача, подцели,
#                шаги, статус) — живёт вместе с задачей;
#   "longterm" — долговременная: профиль пользователя, принятые решения,
#                знания — сохраняется между сессиями.
# Список допустимых типов (используется валидацией в session_store).
MEMORY_TYPES = ("short", "working", "longterm")
# Максимальная длина значения одного элемента памяти (символов), чтобы
# хранилище не разрасталось бесконечно.
MEMORY_VALUE_CAP = 4000
# Максимальное число ключей в рабочей/долговременной памяти.
MEMORY_MAX_KEYS = 200

# --- Цены DeepSeek, юань за 1М токенов (вход без кэша/выход, off-peak) ---
DS_PRICE_INPUT_PER_M = {"deepseek-flash": 1.5}
DS_PRICE_OUTPUT_PER_M = {"deepseek-flash": 4.5}

# --- ПРОФИЛИ (персоны) ---
# Профиль — это именованная «персона» со СВОЕЙ памятью (рабочая +
# долговременная), своим диалогом и настройками. Профили хранятся в файле
# profiles.json, активный профиль определяет, чья память/диалог используется.
# Характер (тон) и характер ответов (формат/длина) задаются ПРИ СОЗДАНИИ
# профиля и подставляются в системный промпт каждой модели.
PROFILES_FILE = os.path.join(SESSION_DIR, "profiles.json")
# Максимальная длина имени профиля и его атрибутов (символов).
PROFILE_NAME_CAP = 60
PROFILE_ATTR_CAP = 400
# Сколько профилей можно создать.
PROFILE_MAX = 50

# --- ИНВАРИАНТЫ (правила, которые ассистент НЕ вправе нарушать) ---
# Инварианты — это ограничения (архитектура, техрешения, стек, бизнес-правила),
# которые хранятся ОТДЕЛЬНО от диалога, явно учитываются ассистентом и при
# конфликте с запросом приводят к ОТКАЗУ с пояснением (см. task2.md).
# Допустимые категории инварианта (упрощают UI и группировку).
INVARIANT_CATEGORIES = (
    ("architecture", "Архитектура"),
    ("tech", "Технические решения"),
    ("stack", "Ограничения по стеку"),
    ("business", "Бизнес-правила"),
)
# Максимальная длина текста одного инварианта (символов).
INVARIANT_TEXT_CAP = 500
# Максимальное число инвариантов.
INVARIANT_MAX = 100
# Модель для ДЕТЕРМИНИРОВАННОЙ пост-проверки ответа на нарушение инвариантов
# (отдельный вызов модели «нарушает ли ответ инвариант?», см. agent.check_invariants).
INVARIANT_CHECK_MODEL = "GigaChat"

# --- MCP (Model Context Protocol) ---
# Возможность использовать инструменты MCP-сервера (list_tools / call_tool).
# В левой колонке интерфейса есть ЧЕКБОКС включения MCP, КНОПКА проверки
# СТАТУСА MCP-сервера и ВЫБОР МОДЕЛИ, которая используется при работе с MCP.
# Сервер поднимается по stdio как подпроцесс. По умолчанию работает режим
# «Авто»: модель выбирает сервер по запросу (git -> project, файлы -> files,
# поддержка -> support). Команду можно переопределить через
# MCP_SERVER_CMD / MCP_SERVER_ARGS.
MCP_ENABLED = False                 # включён ли MCP (стартовое значение)
MCP_SERVER_CMD = ""                 # "" — текущий интерпретатор Python
MCP_SERVER_ARGS = ["project_server.py"]  # аргументы запуска MCP-сервера
MCP_TIMEOUT = 30                    # таймаут операций MCP (секунд)

# Список доступных MCP-серверов для ВЫБОРА в интерфейсе. Каждый элемент:
#   id    — короткий код (хранится в настройках);
#   label — подпись в выпадающем списке;
#   args  — аргументы запуска stdio-подпроцесса (команда — MCP_SERVER_CMD).
# Активный сервер выбирается в UI и сохраняется в MCP_SETTINGS_FILE ("server").
MCP_SERVERS = [
    # --- Задача 1 (docs/Final_task.md): доступ ассистента к проекту ---
    # git branch/status/diff и список файлов (только чтение, без записи).
    {"id": "project", "label": "Проект (git)",
     "args": ["project_server.py"]},
    # --- Задача 3 (docs/Final_task.md): поддержка пользователей ---
    # JSON-тикеты/пользователи; RAG по FAQ — docs/support.
    {"id": "support", "label": "Поддержка (тикеты/CRM)",
     "args": ["support_server.py"]},
    # --- Задача 4 (docs/Final_task.md): работа с файлами проекта ---
    # Чтение/поиск/создание/изменение файлов в sandbox проекта.
    {"id": "files", "label": "Файлы проекта",
     "args": ["files_server.py"]},
]
# Специальный режим: модель САМА выбирает MCP-сервер и инструмент по запросу
# (git -> project, файлы -> files, поддержка -> support).
MCP_AUTO_ID = "auto"
MCP_AUTO_LABEL = "Авто (модель выбирает сервер)"
# MCP-сервер по умолчанию: авто-выбор сервера моделью.
MCP_SERVER_DEFAULT = MCP_AUTO_ID

# --- ПОДДЕРЖКА ПОЛЬЗОВАТЕЛЕЙ (docs/Final_task.md, задача 3) ---
# JSON с вымышленными пользователями/тикетами. Для реальных данных задайте
# SUPPORT_DATA_DIR на папку ВНЕ репозитория (или переменную окружения).
SUPPORT_DATA_DIR = os.path.join(BASE_DIR, "support_data")
# FAQ и документация продукта, по которым работает RAG поддержки.
SUPPORT_DOCS_DIR = os.path.join(BASE_DIR, "docs", "support")
# id MCP-сервера поддержки (см. MCP_SERVERS).
SUPPORT_SERVER_ID = "support"
# Сколько фрагментов FAQ подмешивать в ответ.
SUPPORT_TOP_K = 5
# Максимальный размер блока контекста тикета/пользователя.
SUPPORT_CONTEXT_CHARS = 6000

# --- РАБОТА С ФАЙЛАМИ (docs/Final_task.md, задача 4) ---
# id MCP-сервера «Файлы проекта» (см. MCP_SERVERS).
FILES_SERVER_ID = "files"
# Лимиты sandbox: размер читаемого/записываемого файла, число результатов
# поиска, объём контекста для модели и максимум diff в ответе.
FILES_MAX_FILE_MB = 2
FILES_MAX_READ_CHARS = 200000
FILES_MAX_WRITE_CHARS = 200000
FILES_MAX_RESULTS = 200
FILES_MAX_CONTEXT_CHARS = 16000
FILES_MAX_DIFF_OUTPUT = 24000

# Метка модели, применяемой агентом при работе с MCP. "" — первая доступная.
MCP_MODEL = ""
# Файл настроек MCP между запусками (в папке сессии).
MCP_SETTINGS_FILE = os.path.join(SESSION_DIR, "mcp.json")

# --- RAG (Retrieval-Augmented Generation) ---
# Локальный RAG поверх PDF-документов: папка с PDF индексируется (текстовый
# слой -> чанки -> эмбеддинги через Ollama), а при запросе в чат выполняется
# ПОИСК по индексу и найденный контекст ПОДМЕШИВАЕТСЯ в системный промпт
# (режим A2: поиск всегда при включённом RAG, модель не «решает» сама).
#   * эмбеддинги — ЛОКАЛЬНАЯ модель Ollama nomic-embed-text-v2-moe (dim=768,
#     контекст 512 токенов), внешних API не требуется;
#   * индекс чанков и векторов хранится в SQLite (session/rag_index.db),
#     настройки и метаданные — в JSON (session/rag.json);
#   * из PDF берётся ТОЛЬКО ТЕКСТ (графику игнорируем), OCR не применяется —
#     исходные PDF сконвертированы из Word и имеют текстовый слой.
# RAG-настройки можно менять в интерфейсе (левая колонка, блок «RAG»):
#   enabled  — использовать ли RAG в запросах;
#   docs_dir — настраиваемый путь к папке с PDF;
#   top_k    — сколько фрагментов подмешивать в промпт.
RAG_ENABLED = False
# Путь к папке с PDF-документами по умолчанию (можно менять в UI).
RAG_DOCS_DIR = os.path.join(BASE_DIR, "docs_rag")
# Файл индекса (SQLite): чанки + векторы эмбеддингов.
RAG_INDEX_FILE = os.path.join(SESSION_DIR, "rag_index.db")
# Файл НАСТРОЕК и метаданных RAG (JSON): enabled/docs_dir/top_k + статистика.
RAG_SETTINGS_FILE = os.path.join(SESSION_DIR, "rag.json")
# Локальная модель эмбеддингов Ollama (см. `ollama list`).
RAG_EMBED_MODEL = "nomic-embed-text-v2-moe"
# HTTP-эндпоинт Ollama для эмбеддингов (одного текста за вызов).
RAG_EMBED_URL = "http://127.0.0.1:11434/api/embeddings"
# Размерность вектора модели (nomic-embed-text-v2-moe = 768). Проверка при
# записи/чтении индекса: защита от смены модели эмбеддингов.
RAG_EMBED_DIM = 768
# Контекст модели эмбеддингов — 512 токенов. Чанк делаем заведомо короче,
# чтобы весь текст чанка попадал в вектор (русский ~2-3 символа/токен).
RAG_CHUNK_CHARS = 600
# Перекрытие соседних чанков (символов) — чтобы не терять смысл на границах.
RAG_CHUNK_OVERLAP = 100
# Сколько фрагментов (top-k) подмешивать в промпт при поиске.
RAG_TOP_K = 5
# Сколько символов контекста RAG максимум уходит в промпт (защита от раздувания).
RAG_CTX_CAP = 6000
# Максимальный размер индексируемого PDF (МБ). PDF до 100 МБ «из-за графики»,
# но берём только текстовый слой; файл крупнее — пропускаем с пометкой.
RAG_MAX_PDF_MB = 100
# Таймаут запроса к Ollama (секунд): эмбеддинги одного текста + прогрев модели.
RAG_EMBED_TIMEOUT = 60
# Протокол эмбеддингов: "ollama" (/api/embeddings, fallback /api/embed) либо
# "openai" (/v1/embeddings) — на случай llama.cpp-сервера эмбеддингов.
RAG_EMBED_API = "ollama"
# Необязательный ключ доступа к серверу эмбеддингов (llama.cpp --api-key).
RAG_EMBED_API_KEY = ""

# --- RAG: ДОКУМЕНТЫ И КОД ПРОЕКТА (docs/Final_task.md, задача 1) ---
# Кроме PDF индексируются ТЕКСТОВЫЕ файлы: README, папка docs и исходники.
# Для текстовых файлов номер страницы не имеет смысла — в индексе page=0,
# а идентификатор источника выглядит как «README.md#chunk.3».
RAG_DOC_EXTS = (
    ".pdf",    # документы (текстовый слой, pypdf)
    ".md", ".mdx", ".rst", ".txt",      # документация
    ".py", ".js", ".css", ".html",      # исходный код проекта
    ".json", ".yml", ".yaml", ".toml",  # конфигурация/схемы данных
)
# Максимальный размер индексируемого ТЕКСТОВОГО файла (МБ): защита от
# случайной индексации гигантских логов/дампов.
RAG_DOC_MAX_MB = 2
# Папки, которые НЕ индексируются при обходе (служебные/бинарные/сессии).
# support_data/ — персональные данные тикетов/пользователей: в общий RAG они
# не попадают, для поддержки используется только FAQ (docs/support).
RAG_DOC_SKIP_DIRS = (
    ".git", ".idea", ".vscode", ".venv", "venv", "env",
    "node_modules", "__pycache__", "session", "rag_images", "support_data",
)
# Файлы с СЕКРЕТАМИ: никогда не индексируются (ключи API лежат в корне
# проекта и не должны попадать в базу знаний/ответы модели).
RAG_DOC_SKIP_FILES = (
    "apidpsk.txt", "gigakey.txt", "ya.txt", "exch.txt", "w.txt",
    "weathe.txt",
)


# --- УЛУЧШЕННЫЙ ПОИСК RAG (2-й этап: фильтрация/реранкинг + query rewrite) ---
# После базового векторного поиска применяется ВТОРОЙ этап обработки:
#   * query rewrite   — переформулировка запроса в несколько поисковых
#                       формулировок силами LLM (улучшает попадание);
#   * гибридный поиск — к косинусной близости подмешивается лексический
#                       сигнал (совпадение ключевых слов запроса), что
#                       поднимает специфичные страницы (например, раздел
#                       «Обратная засечка») над страницами общего описания;
#   * фильтрация      — отсечение кандидатов ниже порога близости;
#   * реранкинг       — пересортировка кандидатов (LLM-реранкер либо по
#                       гибридному скору).
# Всё переключается в интерфейсе (блок «RAG»).
# Выполнять ли query rewrite (расширение запроса) через LLM.
RAG_REWRITE = True
# Сколько альтернативных формулировок запроса генерировать при rewrite.
RAG_REWRITE_COUNT = 3
# Модель LLM для query rewrite ("" — модель по умолчанию, GigaChat).
RAG_REWRITE_MODEL = ""
# Использовать ли гибридный поиск (косинус + лексика).
RAG_HYBRID = True
# Вес семантической (косинусной) части в гибридном скоре; вес лексики = 1-w.
RAG_HYBRID_ALPHA = 0.6
# Сколько кандидатов набирать ПЕРЕД фильтрацией/реранкингом (множитель top_k).
RAG_POOL_MULT = 6
# Минимальный порог гибридного скора: кандидаты ниже отбрасываются.
# 0.0 — порог выключен (фильтрация только по top_k).
RAG_MIN_SCORE = 0.0
# Реранкер после поиска: "none" — сортировка по гибридному скору;
# "llm" — пересортировка кандидатов LLM-моделью (дороже, но точнее).
RAG_RERANK = "none"
# Модель LLM-реранкера ("" — модель по умолчанию, GigaChat).
RAG_RERANK_MODEL = ""
# Сколько кандидатов подавать LLM-реранкеру (ограничение стоимости запроса).
RAG_RERANK_CANDIDATES = 12
# Таймаут (секунд) для LLM-вызовов реранкера/rewrite.
RAG_LLM_TIMEOUT = 60


# --- RAG: ИЗОБРАЖЕНИЯ PDF (встроенные картинки) ---
# При индексации, помимо текстового слоя, извлекаем ВСТРОЕННЫЕ изображения
# страниц (pypdf page.images + Pillow; новых зависимостей не требуется).
# Их назначение — вставлять в карточку ответа ФРАГМЕНТ ДОКУМЕНТА: текст
# найденного чанка + картинки соответствующей страницы (текст + картинка
# при наличии связки в RAG).
#   * картинки складываются в RAG_IMAGES_DIR (подпапка на документ, имя —
#     SHA-256 содержимого: дедупликация одинаковых вставок);
#   * ЛОГОТИПЫ/ИКОНКИ/декоративные элементы ОТСЕКАЮТСЯ фильтром по размеру
#     (минимальная сторона и доля площади страницы);
#   * лимиты (на одно изображение и на документ) защищают индекс от роста.
RAG_IMAGES_ENABLED = True
# Папка хранения извлечённых изображений (внутри папки сессии).
RAG_IMAGES_DIR = os.path.join(SESSION_DIR, "rag_images")
# Минимальная сторона изображения (пикселей): меньше — это иконка/логотип,
# картинку пропускаем. Отсекает мелкие декоративные элементы.
RAG_IMAGE_MIN_DIM = 200
# Минимальная доля площади страницы, которую должно занимать изображение
# (для типичной страницы A4). Дополнительный фильтр против логотипов в углу.
RAG_IMAGE_MIN_AREA = 0.02
# Потолок размера одного сохранённого изображения (МБ): крупнее — пропуск.
RAG_IMAGE_MAX_MB = 8
# Сколько изображений максимум хранить на один документ (защита индекса).
RAG_IMAGE_MAX_PER_DOC = 40
# Формат сохранения изображений: "png" (без потерь) или "jpg" (компактнее).
RAG_IMAGE_FORMAT = "png"
# Максимум фрагментов документа (текст + картинки), отдаваемых в карточку
# ответа для блока «Фрагмент документа». 0 — без ограничения.
RAG_FRAGMENTS_MAX = 4


# --- ЛОКАЛЬНАЯ МОДЕЛЬ (llama.cpp / Ollama / LM Studio) ---
# Блок подключения ЛОКАЛЬНОЙ модели: пользователь указывает КОРЕНЬ папки с
# движком и моделями (напр. E:\Models\llama). Сервер:
#   1) СКАНИРУЕТ папку — находит исполняемый файл сервера (llama-server.exe
#      в Windows, llama-server в *nix; а также ollama / LM Studio если есть)
#      и файлы моделей (*.gguf) рекурсивно;
#   2) предлагает ПАРАМЕТРЫ ЗАПУСКА (модель, host, port, ctx, ngl, threads,
#      flash-attn, api-key) с АВТОПОДБОРОМ (ngl — по объёму видеопамяти),
#      доступные для редактирования;
#   3) ЗАПУСКАЕТ сервер подпроцессом по кнопке и отдаёт URL вида
#      http://localhost:8080/v1 (OpenAI-совместимый /v1/chat/completions).
# Остановка — ВРУЧНУЮ (кнопкой), без авто-idle-стопа.
# Сам блок выключен по умолчанию; запуск сервера — по кнопке.
LOCAL_ENABLED = False
# Корень папки с движком/моделями (пользователь задаёт в интерфейсе).
LOCAL_ROOT = ""
# Настройки/состояние блока между запусками (в папке сессии).
LOCAL_SETTINGS_FILE = os.path.join(SESSION_DIR, "local_model.json")
# Имя исполняемого файла сервера llama.cpp по платформам.
# Windows — llama-server.exe; Linux/macOS — llama-server.
LOCAL_SERVER_EXE_WIN = "llama-server.exe"
LOCAL_SERVER_EXE_NIX = "llama-server"
# Расширение файлов моделей (GGUF).
LOCAL_MODEL_EXT = ".gguf"
# Хост прослушивания локального сервера (только локально).
LOCAL_HOST = "127.0.0.1"
# Порт по умолчанию (llama-server по умолчанию тоже 8080).
LOCAL_PORT = 8080
# Размер контекста (токенов) по умолчанию (llama-server: -c/--ctx-size).
LOCAL_CTX = 8192
# Число слоёв на GPU (llama.cpp: -ngl). 0 — автоподбор по видеопамяти.
LOCAL_NGL = 0
# Число CPU-потоков (llama.cpp: -t). 0 — авто (по числу физ. ядер).
LOCAL_THREADS = 0
# Flash Attention (llama.cpp: -fa/--flash-attn): "auto" | "on" | "off".
LOCAL_FLASH_ATTN = "auto"
# Разрешить использование Jinja-шаблона чата (llama.cpp: --jinja). Для
# корректной работы tool/reasoning-шаблонов современных моделей.
LOCAL_JINJA = True
# API-ключ (локально обычно НЕ нужен; пусто — без ключа). llama.cpp: --api-key.
LOCAL_API_KEY = ""
# Метка локальной модели для селектора ответов и карточки.
LOCAL_LABEL = "Local"
# CSS-класс карточки локальной модели.
LOCAL_CLS = "local-llm"
# Таймаут запроса к локальной модели (секунд): генерация на GPU быстрее, но
# при частичной выгрузке на CPU может быть медленной.
LOCAL_TIMEOUT = 180
# Таймаут ОЖИДАНИЯ готовности сервера после запуска (секунд): загрузка
# 9B-модели в видеопамять занимает 10–60 с.
LOCAL_START_TIMEOUT = 180
# Сколько свободной видеопамяти (МБ) оставлять в запасе при автоподборе -ngl.
LOCAL_VRAM_RESERVE_MB = 512

# --- МОДЕЛЬ ЭМБЕДДИНГОВ RAG (Ollama на ПК/VPS) ---
# Блок «Модель эмбеддингов» в левой колонке запускает/переиспользует Ollama
# так же, как блок локальной модели запускает llama-server: путь -> скан ->
# выбор модели -> запуск. Настройки движка хранятся ОТДЕЛЬНО от RAG: эмбеддинги
# одни на сервер, а включение/выключение RAG не должно менять выбранную модель.
EMBED_ENABLED = False
# Файл настроек/состояния блока (в папке сессии).
EMBED_SETTINGS_FILE = os.path.join(SESSION_DIR, "embed_model.json")
# Адрес локальной Ollama (на ПК-агенте и на VPS).
EMBED_LOCAL_HOST = "127.0.0.1"
EMBED_LOCAL_PORT = 11434
# Корень для поиска Ollama: на VPS — /opt/models, на ПК — e:/Models.
EMBED_LOCAL_ROOT = "/opt/models"
# Корень на ПК (скан/запуск через ПК-агент). Пусто -> RTK_PC_ROOT.
EMBED_PC_ROOT = ""
# Таймаут ожидания готовности Ollama после запуска (секунд).
EMBED_START_TIMEOUT = 60
# Если Ollama уже запущена (трей/вручную) — использовать её, новую не
# поднимать (экономия ресурсов).
EMBED_REUSE_RUNNING = True
# Признаки embedding-моделей в имени (для пометки ДО запуска Ollama).
EMBED_MODEL_HINTS = ("embed", "bge", "e5", "gte", "jina", "nomic",
                     "mxbai", "arctic", "snowflake")


# ===========================================================================
# ENV-КОНФИГ (VPS / серверный режим)
# ===========================================================================
# Значения ниже переопределяют дефолты только если соответствующая переменная
# окружения задана и не пуста. Локальный запуск без ENV работает как раньше.
#
#   RTK_HOST=127.0.0.1            app слушает loopback, наружу прячет nginx
#   RTK_PORT=8000                 внутренний порт app
#   RTK_OPEN_BROWSER=0            на сервере браузер не открываем
#   RTK_BEHIND_PROXY=1            доверять X-Forwarded-For
#   RTK_SESSION_DIR=...           папка сессий вне репозитория
#   RTK_MAX_SESSIONS=8            лимит активных сессий (0 = выключить)
#   RTK_IDLE_TIMEOUT=60           ACTIVE -> CHECK
#   RTK_CHECK_TIMEOUT=30          CHECK -> OFFLINE
#   RTK_RATE_LIMIT_PER_MIN=10     запросов/мин на IP (0 = выключить)
#   RTK_OWNER_TOKEN=<секрет>      приоритетный слот владельца
#   RTK_SHARED_RAG=1              один индекс RAG на все сессии
#   RTK_LOCAL_HOST/PORT/API_KEY   уже запущенный llama-server (systemd)
#   RTK_WEAK_LABEL=Qwen-2B        слабая модель VPS
#   RTK_STRONG_LABEL=Qwen-9B      сильная модель ПК (через туннель)
#   RTK_TUNNEL_HOST/PORT          loopback-адрес движка ПК на VPS
#   RTK_AGENT_HOST/PORT           loopback-адрес служебного API ПК-агента
#   RTK_EMBED_TUNNEL_PORT=11435   loopback-порт Ollama ПК на VPS
#   RTK_PC_EMBED_ROOT=e:/Models  корень моделей эмбеддингов на ПК
#   RTK_EMBED_LOCAL_PORT=11434    локальный порт Ollama (VPS/ПК)
#   RTK_AUTO_STRONG=0             не переключать weak->strong автоматически
#   RAG_EMBED_URL=...             эндпоинт эмбеддингов (Ollama через туннель)
#   RAG_EMBED_API=ollama          протокол эмбеддингов (ollama/openai)


def _env_str(name, default=""):
    """Строка из ENV; пустое значение -> default."""
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val)


def _env_int(name, default):
    """Целое из ENV; пусто/ошибка -> default."""
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    """Булево из ENV: 1/true/yes/on -> True."""
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# --- Web / сервер ---
WEB_HOST = _env_str("RTK_HOST", WEB_HOST)
WEB_PORT = _env_int("RTK_PORT", WEB_PORT)
OPEN_BROWSER = _env_bool("RTK_OPEN_BROWSER", True)
BEHIND_PROXY = _env_bool("RTK_BEHIND_PROXY", False)
SKIP_KEY_CHECK = _env_bool("RTK_SKIP_KEY_CHECK", False)
CHECK_NETWORK = _env_bool("RTK_CHECK_NETWORK", True)

# --- Сессии (VPS) ---
SESSION_DIR = _env_str("RTK_SESSION_DIR", SESSION_DIR)
SESSION_FILE = os.path.join(SESSION_DIR, "session.json")
PROFILES_FILE = os.path.join(SESSION_DIR, "profiles.json")
MCP_SETTINGS_FILE = os.path.join(SESSION_DIR, "mcp.json")
LOCAL_SETTINGS_FILE = os.path.join(SESSION_DIR, "local_model.json")
RAG_SETTINGS_FILE = os.path.join(SESSION_DIR, "rag.json")
RAG_INDEX_FILE = os.path.join(SESSION_DIR, "rag_index.db")
RAG_IMAGES_DIR = os.path.join(SESSION_DIR, "rag_images")
EMBED_SETTINGS_FILE = os.path.join(SESSION_DIR, "embed_model.json")

# 0 — лимит выключен (локальный режим, одна общая сессия как раньше).
MAX_SESSIONS = _env_int("RTK_MAX_SESSIONS", 0)
IDLE_TIMEOUT = _env_int("RTK_IDLE_TIMEOUT", 60)
CHECK_TIMEOUT = _env_int("RTK_CHECK_TIMEOUT", 30)
SESSION_GC_PERIOD = _env_int("RTK_SESSION_GC_PERIOD", 30)
OWNER_TOKEN = _env_str("RTK_OWNER_TOKEN", "")
OWNER_COOKIE = "owner"
# 1 — один общий индекс RAG для всех клиентских сессий (нужно на VPS).
SHARED_RAG = _env_bool("RTK_SHARED_RAG", False)

# --- Ограничения запросов ---
RATE_LIMIT_PER_MIN = _env_int("RTK_RATE_LIMIT_PER_MIN", 0)
MAX_QUESTION_CHARS = _env_int("RTK_MAX_QUESTION_CHARS", 8000)
MAX_BODY_BYTES = _env_int("RTK_MAX_BODY_BYTES", 256 * 1024)

# --- Локальная модель VPS (внешний llama-server) ---
LOCAL_HOST = _env_str("RTK_LOCAL_HOST", LOCAL_HOST)
LOCAL_PORT = _env_int("RTK_LOCAL_PORT", LOCAL_PORT)
LOCAL_CTX = _env_int("RTK_LOCAL_CTX", LOCAL_CTX)
LOCAL_API_KEY = _env_str("RTK_LOCAL_API_KEY", LOCAL_API_KEY)
LOCAL_LABEL = _env_str("RTK_LOCAL_LABEL", LOCAL_LABEL)
LOCAL_TIMEOUT = _env_int("RTK_LOCAL_TIMEOUT", LOCAL_TIMEOUT)

# --- Туннель к сильной модели ПК ---
TUNNEL_HOST = _env_str("RTK_TUNNEL_HOST", "127.0.0.1")
TUNNEL_PORT = _env_int("RTK_TUNNEL_PORT", 18080)
AGENT_HOST = _env_str("RTK_AGENT_HOST", "127.0.0.1")
AGENT_PORT = _env_int("RTK_AGENT_PORT", 18081)
TUNNEL_SSH = _env_str("RTK_TUNNEL_SSH", "")
# Путь к приватному ключу ПК для готовой команды в UI.
TUNNEL_IDENTITY = _env_str(
    "RTK_TUNNEL_IDENTITY", "%USERPROFILE%\\.ssh\\rtk_pc")
# Корень папки с моделями НА ПК (для команды агента и скана ПК).
PC_ROOT = _env_str("RTK_PC_ROOT", "")
WEAK_LABEL = _env_str("RTK_WEAK_LABEL", "Qwen-2B")
STRONG_LABEL = _env_str("RTK_STRONG_LABEL", "Qwen-9B")
CONFIRM_TIMEOUT = _env_int("RTK_CONFIRM_TIMEOUT", 120)
TUNNEL_CHECK_PERIOD = _env_int("RTK_TUNNEL_CHECK_PERIOD", 20)
# Автоматически переключать слабую модель на сильную при восстановлении
# туннеля. По умолчанию ВЫКЛЮЧЕНО: модели запускает/выбирает пользователь.
# Обратный авто-возврат на слабую при обрыве туннеля остаётся.
AUTO_STRONG = _env_bool("RTK_AUTO_STRONG", False)
AGENT_TIMEOUT = _env_int("RTK_AGENT_TIMEOUT", 2)
TUNNEL_PROBE_TIMEOUT = _env_int("RTK_TUNNEL_PROBE_TIMEOUT", 2)

# --- Ollama-эмбеддинги через туннель с ПК ---
# Порт Ollama НА ПК (нужен только для команды запуска ПК-агента).
OLLAMA_PORT = _env_int("RTK_OLLAMA_PORT", 11434)
EMBED_TUNNEL_HOST = _env_str("RTK_EMBED_TUNNEL_HOST", "127.0.0.1")
EMBED_TUNNEL_PORT = _env_int("RTK_EMBED_TUNNEL_PORT", 11435)
EMBED_PROBE_TIMEOUT = _env_int("RTK_EMBED_PROBE_TIMEOUT", 5)
# По умолчанию — локальная Ollama на этом же хосте (11434). На VPS
# переопределяется на loopback-порт туннеля к Ollama ПК (11435).
RAG_EMBED_URL = _env_str(
    "RAG_EMBED_URL", "http://127.0.0.1:11434/api/embeddings")
RAG_EMBED_MODEL = _env_str("RAG_EMBED_MODEL", RAG_EMBED_MODEL)
RAG_EMBED_DIM = _env_int("RAG_EMBED_DIM", RAG_EMBED_DIM)
RAG_EMBED_TIMEOUT = _env_int("RAG_EMBED_TIMEOUT", RAG_EMBED_TIMEOUT)
RAG_EMBED_API = _env_str("RAG_EMBED_API", RAG_EMBED_API)
RAG_EMBED_API_KEY = _env_str("RAG_EMBED_API_KEY", RAG_EMBED_API_KEY)

# --- Модель эмбеддингов RAG (Ollama) ---
# Локальный порт Ollama на этом хосте (VPS или ПК-агент).
EMBED_LOCAL_HOST = _env_str("RTK_EMBED_LOCAL_HOST", EMBED_LOCAL_HOST)
EMBED_LOCAL_PORT = _env_int("RTK_EMBED_LOCAL_PORT", EMBED_LOCAL_PORT)
# Корень для сканирования: VPS — /opt/models, ПК — e:/Models.
EMBED_LOCAL_ROOT = _env_str("RTK_EMBED_LOCAL_ROOT", EMBED_LOCAL_ROOT)
EMBED_PC_ROOT = _env_str("RTK_PC_EMBED_ROOT", EMBED_PC_ROOT or PC_ROOT)
EMBED_START_TIMEOUT = _env_int("RTK_EMBED_START_TIMEOUT", EMBED_START_TIMEOUT)
EMBED_REUSE_RUNNING = _env_bool("RTK_EMBED_REUSE_RUNNING", EMBED_REUSE_RUNNING)
RAG_REWRITE = _env_bool("RAG_REWRITE", RAG_REWRITE)
RAG_RERANK = _env_str("RAG_RERANK", RAG_RERANK)
RAG_HYBRID = _env_bool("RAG_HYBRID", RAG_HYBRID)
RAG_MIN_SCORE = float(_env_str("RAG_MIN_SCORE", str(RAG_MIN_SCORE)))
RAG_DOCS_DIR = _env_str("RTK_RAG_DOCS_DIR", RAG_DOCS_DIR)
# Куда клонировать git-репозитории для RAG (index_repo).
RAG_REPO_DIR = _env_str("RTK_RAG_REPO_DIR",
                        os.path.join(SESSION_DIR, "repos"))

