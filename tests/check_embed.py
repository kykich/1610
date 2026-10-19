# -*- coding: utf-8 -*-
"""Автономная проверка блока «Модель эмбеддингов RAG» (Ollama).

Проверяется БЕЗ запуска Ollama и без сети:
  1. Поиск установки Ollama и каталога моделей (manifests/blobs);
  2. Разбор манифестов: имя, размер, признак embedding-модели;
  3. Пробы /api/tags, /api/ps и тестового эмбеддинга (замоканы);
  4. REUSE-FIRST: уже запущенная Ollama используется, Popen не вызывается;
  5. needs_choice, если выбранной модели в запущенной Ollama нет;
  6. stop не останавливает внешнюю Ollama;
  7. active_embed_config: файл настроек -> ENV-фолбэк;
  8. rag.embed_text: протоколы Ollama (/api/embeddings) и OpenAI (/v1/embeddings);
  9. Метаданные индекса и предупреждение embed_mismatch;
 10. /api/embed: снимок состояния с reuse_offer.

Запуск: python -m tests.check_embed
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import config, ollama_server, rag  # noqa: E402
import web.server as web_server  # noqa: E402


def _tmpdir():
    return tempfile.mkdtemp(prefix="embed_test_")


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def _make_ollama_tree(root):
    """Создаёт e:/Models-подобное дерево: ollama + models/manifests+blobs."""
    exe_dir = os.path.join(root, "ollama")
    os.makedirs(exe_dir, exist_ok=True)
    exe = os.path.join(exe_dir, "ollama.exe")
    with open(exe, "wb") as fh:
        fh.write(b"stub")
    models_dir = os.path.join(exe_dir, "models")
    manifest = os.path.join(
        models_dir, "manifests", "registry.ollama.ai", "library",
        "nomic-embed-text", "latest")
    _write(manifest, {"schemaVersion": 2, "layers": [
        {"mediaType": "application/vnd.ollama.image.model",
         "digest": "sha256:abc", "size": 2 * 1024 * 1024}]})
    os.makedirs(os.path.join(models_dir, "blobs"), exist_ok=True)
    return exe, models_dir


def test_scan_and_manifest():
    section("1. Скан корня и разбор манифестов")
    root = _tmpdir()
    exe, models_dir = _make_ollama_tree(root)
    found = ollama_server.find_ollama(root)
    check("ollama.exe найден", found == exe, str(found))
    discovered = ollama_server.discover_models_dir(root, found)
    check("каталог моделей найден (manifests+blobs)",
          os.path.normcase(discovered) == os.path.normcase(models_dir),
          str(discovered))
    models = ollama_server.list_models(discovered)
    check("модель прочитана из манифеста", len(models) == 1)
    check("имя модели с тегом",
          bool(models) and models[0]["name"] == "nomic-embed-text:latest",
          str(models))
    check("embedding-модель помечена", bool(models) and models[0]["embedding"])
    check("размер посчитан по layers",
          bool(models) and models[0]["size_mb"] == 2,
          str(models))
    scan = ollama_server.scan_root(root)
    check("scan_root ok", bool(scan.get("ok")))
    check("scan_root вернул сервер ollama",
          any(s.get("kind") == "ollama" for s in scan.get("servers") or []))
    check("scan_root вернул models_dir",
          os.path.normcase(scan.get("models_dir")) == os.path.normcase(
              models_dir))


def test_probes():
    section("2. Пробы Ollama (замоканы)")
    orig_get = ollama_server._http_get_json
    orig_post = ollama_server._http_post_json
    orig_ps = ollama_server.probe_ps

    tags = {"models": [{
        "name": "nomic-embed-text-v2-moe:latest",
        "size": 10 * 1024 * 1024,
        "capabilities": ["embedding"],
        "details": {"embedding_length": 768, "family": "nomic-bert-moe",
                    "parameter_size": "475.29M", "quantization_level": "F16"},
    }]}
    ollama_server._http_get_json = lambda url, timeout=None: tags
    try:
        pr = ollama_server.probe()
    finally:
        ollama_server._http_get_json = orig_get
    check("probe: Ollama доступна", pr.get("alive") is True)
    m = (pr.get("models") or [{}])[0]
    check("probe: capabilities разобраны", m.get("embedding") is True)
    check("probe: embedding_length=768", m.get("embedding_length") == 768)

    # probe_model: /api/embeddings -> вектор; /api/ps -> модель загружена.
    ollama_server._http_post_json = (
        lambda url, payload, timeout=None: {"embedding": [0.1] * 768})
    ollama_server.probe_ps = lambda url=None, timeout=None: {
        "alive": True,
        "models": [{"name": "nomic-embed-text-v2-moe:latest"}]}
    try:
        pm = ollama_server.probe_model("nomic-embed-text-v2-moe:latest")
    finally:
        ollama_server._http_post_json = orig_post
        ollama_server.probe_ps = orig_ps
    check("probe_model ok", pm.get("ok") is True)
    check("probe_model dim=768", pm.get("dim") == 768)
    check("probe_model: модель уже в памяти", pm.get("loaded") is True)


class FakeMgr(object):
    """Фейковый менеджер Ollama для тестов reuse-first."""

    def __init__(self, settings):
        self.settings = dict(settings)

    def load_settings(self):
        return dict(self.settings)

    def save_settings(self, s):
        self.settings = dict(s)
        return True

    def status(self):
        return {
            "running": True, "ready": True, "external": True, "pid": 4242,
            "url": "http://127.0.0.1:11434", "port": 11434,
            "models": [{
                "name": "nomic-embed-text-v2-moe:latest",
                "embedding": True, "embedding_length": 768,
                "loaded": True, "capabilities": ["embedding"],
            }],
            "loaded_models": [{"name": "nomic-embed-text-v2-moe:latest"}],
            "error": None, "models_dir": "e:/Models/ollama/models",
        }


def test_reuse_first():
    section("3. Reuse-first: запущенная Ollama используется")
    root = _tmpdir()
    settings_file = os.path.join(root, "embed.json")
    mgr = ollama_server.OllamaManager(settings_file=settings_file)

    orig_probe = ollama_server.probe
    orig_pid = ollama_server._find_pid_on_port
    orig_popen = ollama_server.subprocess.Popen

    def fake_probe(url=None, timeout=None):
        return {"alive": True, "error": None,
                "url": url or "http://127.0.0.1:11434",
                "models": [{
                    "name": "nomic-embed-text-v2-moe:latest",
                    "embedding": True, "embedding_length": 768,
                    "capabilities": ["embedding"],
                }]}

    def no_popen(*a, **k):
        raise AssertionError("Popen не должен вызываться при reuse")

    ollama_server.probe = fake_probe
    ollama_server._find_pid_on_port = lambda port: 4242
    ollama_server.subprocess.Popen = no_popen
    try:
        res = mgr.start(exe="ollama.exe",
                        models_dir="e:/Models/ollama/models",
                        model="nomic-embed-text-v2-moe:latest")
    finally:
        ollama_server.probe = orig_probe
        ollama_server._find_pid_on_port = orig_pid
        ollama_server.subprocess.Popen = orig_popen
    check("start: ok", res.get("ok") is True, str(res))
    check("start: already", res.get("already") is True)
    check("start: reused", res.get("reused") is True)
    check("start: external", res.get("external") is True)
    check("start: модель", res.get("model") == "nomic-embed-text-v2-moe:latest")
    check("start: dim из /api/tags", res.get("dim") == 768)
    saved = mgr.load_settings()
    check("настройки: движок включён", saved.get("enabled") is True)
    check("настройки: backend=ollama", saved.get("backend") == "ollama")
    check("настройки: URL локальной Ollama",
          saved.get("url", "").endswith("/api/embeddings"),
          str(saved.get("url")))

    # Выбранной модели нет в запущенной Ollama -> needs_choice, без Popen.
    ollama_server.probe = lambda url=None, timeout=None: {
        "alive": True, "error": None, "url": url,
        "models": [{"name": "other:latest", "embedding": False}]}
    ollama_server.subprocess.Popen = no_popen
    try:
        bad = mgr.start(model="nomic-embed-text-v2-moe:latest")
    finally:
        ollama_server.probe = orig_probe
        ollama_server.subprocess.Popen = orig_popen
    check("start: needs_choice при отсутствии модели",
          bad.get("needs_choice") is True, str(bad))
    check("start: новый процесс не запускался", bad.get("ok") is False)

    # stop внешней Ollama не убивает процесс и не выключает движок.
    ollama_server.probe = fake_probe
    try:
        st = mgr.stop()
    finally:
        ollama_server.probe = orig_probe
    check("stop: внешняя Ollama не остановлена", st.get("external") is True)
    check("stop: настройки остались включены",
          mgr.load_settings().get("enabled") is True)


def test_active_config():
    section("4. active_embed_config: файл -> ENV")
    root = _tmpdir()
    path = os.path.join(root, "embed.json")
    orig_path = config.EMBED_SETTINGS_FILE
    orig_url = config.RAG_EMBED_URL
    orig_model = config.RAG_EMBED_MODEL
    orig_dim = config.RAG_EMBED_DIM
    config.EMBED_SETTINGS_FILE = path
    try:
        ollama_server.save_settings({
            "enabled": True, "managed": True, "backend": "ollama",
            "api": "ollama", "url": "http://127.0.0.1:11435/api/embeddings",
            "model": "nomic-embed-text-v2-moe:latest", "dim": 768,
            "source": "pc"}, path=path)
        cfg = ollama_server.active_embed_config()
        check("конфиг: backend=ollama", cfg.get("backend") == "ollama")
        check("конфиг: url из файла",
              cfg.get("url") == "http://127.0.0.1:11435/api/embeddings")
        check("конфиг: model из файла",
              cfg.get("model") == "nomic-embed-text-v2-moe:latest")
        check("конфиг: source=pc", cfg.get("source") == "pc")

        ollama_server.save_settings({
            "enabled": False, "managed": True, "backend": "none",
            "url": "", "model": "", "dim": 768}, path=path)
        off = ollama_server.active_embed_config()
        check("остановленный движок -> backend=none",
              off.get("backend") == "none")

        os.remove(path)
        config.RAG_EMBED_URL = "http://127.0.0.1:59999/api/embeddings"
        config.RAG_EMBED_MODEL = "fallback-model"
        config.RAG_EMBED_DIM = 384
        fb = ollama_server.active_embed_config()
        check("без файла -> URL из config",
              fb.get("url") == "http://127.0.0.1:59999/api/embeddings")
        check("без файла -> model из config",
              fb.get("model") == "fallback-model")
        check("без файла -> dim из config", fb.get("dim") == 384)
    finally:
        config.EMBED_SETTINGS_FILE = orig_path
        config.RAG_EMBED_URL = orig_url
        config.RAG_EMBED_MODEL = orig_model
        config.RAG_EMBED_DIM = orig_dim


def test_rag_embed_protocols():
    section("5. rag.embed_text: Ollama и OpenAI-протоколы")
    root = _tmpdir()
    path = os.path.join(root, "embed.json")
    orig_path = config.EMBED_SETTINGS_FILE
    orig_post = rag._post_embed
    config.EMBED_SETTINGS_FILE = path
    rag._embed_cfg_cache = {"key": None, "cfg": None}
    captured = {}

    def fake_post(url, payload, timeout, api_key=""):
        captured["url"] = url
        captured["payload"] = payload
        if url.endswith("/v1/embeddings"):
            return {"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}
        return {"embedding": [0.5] * 768}

    rag._post_embed = fake_post
    try:
        ollama_server.save_settings({
            "enabled": True, "managed": True, "backend": "ollama",
            "api": "ollama", "url": "http://127.0.0.1:11435/api/embeddings",
            "model": "nomic-embed-text-v2-moe:latest", "dim": 768,
            "source": "pc"}, path=path)
        rag._embed_cfg_cache = {"key": None, "cfg": None}
        vec = rag.embed_text("привет")
        check("Ollama: вектор получен", len(vec) == 768)
        check("Ollama: payload.prompt",
              captured.get("payload", {}).get("prompt") == "привет",
              str(captured.get("payload")))
        check("Ollama: модель из настроек",
              captured.get("payload", {}).get("model")
              == "nomic-embed-text-v2-moe:latest")

        ollama_server.save_settings({
            "enabled": True, "managed": True, "backend": "ollama",
            "api": "openai", "url": "http://127.0.0.1:8081/v1/embeddings",
            "model": "bge-m3", "dim": 4, "source": "vps"}, path=path)
        rag._embed_cfg_cache = {"key": None, "cfg": None}
        vec2 = rag.embed_text("привет")
        check("OpenAI: вектор получен", len(vec2) == 4)
        check("OpenAI: payload.input",
              captured.get("payload", {}).get("input") == "привет",
              str(captured.get("payload")))

        ollama_server.save_settings({
            "enabled": False, "managed": True, "backend": "none",
            "url": "", "model": "", "dim": 768}, path=path)
        rag._embed_cfg_cache = {"key": None, "cfg": None}
        raised = False
        try:
            rag.embed_text("привет")
        except RuntimeError:
            raised = True
        check("остановленный движок -> понятная ошибка", raised)
    finally:
        config.EMBED_SETTINGS_FILE = orig_path
        rag._post_embed = orig_post
        rag._embed_cfg_cache = {"key": None, "cfg": None}


def test_index_meta_and_mismatch():
    section("6. Метаданные индекса и embed_mismatch")
    root = _tmpdir()
    path = os.path.join(root, "embed.json")
    orig_path = config.EMBED_SETTINGS_FILE
    config.EMBED_SETTINGS_FILE = path
    rag._embed_cfg_cache = {"key": None, "cfg": None}
    try:
        ollama_server.save_settings({
            "enabled": True, "managed": True, "backend": "ollama",
            "api": "ollama", "url": "http://127.0.0.1:11435/api/embeddings",
            "model": "nomic-embed-text-v2-moe:latest", "dim": 768,
            "source": "pc"}, path=path)
        store = rag.RagStore(
            index_file=os.path.join(root, "index.db"),
            settings_file=os.path.join(root, "rag.json"))
        st = store.status()
        check("status: embed_model из активного движка",
              st.get("embed_model") == "nomic-embed-text-v2-moe:latest")
        check("status: embed_dim=768", st.get("embed_dim") == 768)
        check("status: старый индекс без меты — без mismatch",
              st.get("embed_mismatch") is False)

        conn = store._conn()
        store._meta_set(conn, "embed_model", "other-model")
        store._meta_set(conn, "embed_dim", "1024")
        conn.commit()
        mm = store._embed_mismatch()
        check("mismatch обнаружен", mm.get("mismatch") is True)
        st2 = store.status()
        check("status: mismatch=true", st2.get("embed_mismatch") is True)
        check("status: index_embed_model",
              st2.get("index_embed_model") == "other-model")
        check("status: index_embed_dim=1024",
              st2.get("index_embed_dim") == 1024)

        # update_settings не должен затирать модель эмбеддингов из config.
        store.update_settings(top_k=7)
        check("update_settings не перезаписал активную модель",
              ollama_server.active_embed_config().get("model")
              == "nomic-embed-text-v2-moe:latest")

        # Смена модели -> принудительная переиндексация всех файлов.
        docs = os.path.join(root, "docs")
        os.makedirs(docs, exist_ok=True)
        with open(os.path.join(docs, "note.md"), "w", encoding="utf-8") as fh:
            fh.write("текст для переиндексации")
        ollama_server.save_settings({
            "enabled": True, "managed": True, "backend": "ollama",
            "api": "ollama", "url": "http://127.0.0.1:11435/api/embeddings",
            "model": "new-embed-model", "dim": 4, "source": "vps"}, path=path)
        rag._embed_cfg_cache = {"key": None, "cfg": None}
        orig_embed = rag.embed_text
        rag.embed_text = lambda text: [0.1, 0.2, 0.3, 0.4]
        try:
            report = store.index_docs(docs_dir=docs, verbose=False)
        finally:
            rag.embed_text = orig_embed
        check("index_docs: forced_reindex=True",
              report.get("forced_reindex") is True, str(report))
        check("index_docs: файл реально переиндексирован",
              report.get("files") == 1 and report.get("chunks") == 1,
              str(report))
        conn = store._conn()
        check("после переиндексации meta.embed_model обновлена",
              store._meta_get(conn, "embed_model") == "new-embed-model")
        check("после переиндексации meta.embed_dim=4",
              store._meta_get(conn, "embed_dim") == "4")
        check("после переиндексации mismatch снят",
              store.status().get("embed_mismatch") is False)
    finally:
        config.EMBED_SETTINGS_FILE = orig_path
        rag._embed_cfg_cache = {"key": None, "cfg": None}


def test_web_state_payload():
    section("7. /api/embed: снимок с reuse_offer")
    root = _tmpdir()
    path = os.path.join(root, "embed.json")
    orig_path = config.EMBED_SETTINGS_FILE
    orig_mgr = web_server._embed_manager
    config.EMBED_SETTINGS_FILE = path
    rag._embed_cfg_cache = {"key": None, "cfg": None}
    ollama_server.save_settings({
        "enabled": True, "managed": True, "backend": "ollama",
        "api": "ollama", "url": "http://127.0.0.1:11434/api/embeddings",
        "model": "nomic-embed-text-v2-moe:latest", "dim": 768,
        "source": "vps"}, path=path)
    fake = FakeMgr({"root": "e:/Models", "source": "vps",
                    "model": "nomic-embed-text-v2-moe:latest",
                    "models_dir": "e:/Models/ollama/models"})
    web_server._embed_manager = lambda: fake
    try:
        payload = web_server._embed_state_payload()
    finally:
        web_server._embed_manager = orig_mgr
        config.EMBED_SETTINGS_FILE = orig_path
        rag._embed_cfg_cache = {"key": None, "cfg": None}
    offer = payload.get("reuse_offer") or {}
    check("reuse_offer сформирован", offer.get("available") is True)
    check("reuse_offer: external", offer.get("external") is True)
    check("reuse_offer: PID внешнего процесса", offer.get("pid") == 4242)
    check("reuse_offer: модель выбрана",
          offer.get("selected_model_present") is True)
    check("reuse_offer: embedding-модели",
          len(offer.get("embedding_models") or []) == 1)
    check("reuse_offer: модель в памяти",
          "nomic-embed-text-v2-moe:latest" in (offer.get("loaded") or []))


def main():
    ensure_utf8()
    test_scan_and_manifest()
    test_probes()
    test_reuse_first()
    test_active_config()
    test_rag_embed_protocols()
    test_index_meta_and_mismatch()
    test_web_state_payload()
    return finish("Проверка модели эмбеддингов RAG (Ollama)")


if __name__ == "__main__":
    raise SystemExit(main())






