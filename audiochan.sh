#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
export UV_PYTHON_INSTALL_DIR="$PWD/.python"
export UV_CACHE_DIR="$PWD/var/uv-cache"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
export PYTHONUTF8=1
if ! command -v uv >/dev/null 2>&1; then
    echo 'Нужен uv с https://docs.astral.sh/uv/ — зависимости останутся в .venv проекта.' >&2
    exit 1
fi
uv sync --frozen --no-dev --python 3.13 --managed-python
if [ "$#" -eq 0 ]; then set -- serve; fi
exec .venv/bin/python -m archive "$@"
