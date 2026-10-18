#!/bin/sh
# Индексация проекта 1610 в RAG. Эмбеддинги считает Ollama на ПК,
# доступная на VPS через reverse-туннель (127.0.0.1:11435).
#
# Запуск на VPS:
#   sudo -u rtk sh /opt/rtk1610/deploy/index-rag.sh
#   # или в фоне:
#   sudo -u rtk nohup sh /opt/rtk1610/deploy/index-rag.sh \
#       > /var/lib/rtk1610/index.log 2>&1 &
set -e

APP_DIR="${APP_DIR:-/opt/rtk1610}"
SESSION_DIR="${SESSION_DIR:-/var/lib/rtk1610/session}"

export RTK_SESSION_DIR="$SESSION_DIR"
export RTK_RAG_DOCS_DIR="$APP_DIR"
export RAG_EMBED_URL="${RAG_EMBED_URL:-http://127.0.0.1:11435/api/embeddings}"
export RAG_EMBED_MODEL="${RAG_EMBED_MODEL:-nomic-embed-text-v2-moe}"
export RAG_EMBED_DIM="${RAG_EMBED_DIM:-768}"
export RAG_EMBED_TIMEOUT="${RAG_EMBED_TIMEOUT:-120}"
export RAG_REWRITE="${RAG_REWRITE:-0}"
export RAG_RERANK="${RAG_RERANK:-none}"

cd "$APP_DIR"
exec .venv/bin/python -c \
  "import sys; from rtk_app.rag import RagStore; print(RagStore().index_docs(sys.argv[1], verbose=True))" \
  "$APP_DIR"
