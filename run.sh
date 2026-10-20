#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
poetry_bin="${HOME}/.local/bin/poetry"
if [ ! -x "$poetry_bin" ]; then poetry_bin="$(command -v poetry)"; fi
case "${1:-}" in
  --app) shift; exec "$poetry_bin" run python app.py "$@" ;;
  --desktop) shift; exec "$poetry_bin" run python app.py --desktop "$@" ;;
  *) exec "$poetry_bin" run python -m intellicage "$@" ;;
esac
