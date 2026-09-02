#!/usr/bin/env sh
cd "$(dirname "$0")" && exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
