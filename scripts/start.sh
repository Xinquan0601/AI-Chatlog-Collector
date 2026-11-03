#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
PY=$(printenv AGENTTRAIL_PYTHON || true)
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PY=python3
  else
    PY=python
  fi
fi
exec "$PY" app.py "$@"
