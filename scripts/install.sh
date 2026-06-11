#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

if command -v python3 >/dev/null 2>&1; then
  python3 -m pip install -e .
elif command -v python >/dev/null 2>&1; then
  python -m pip install -e .
else
  echo "Python 3.11+ is required but was not found." >&2
  exit 1
fi

if command -v wj >/dev/null 2>&1; then
  exec wj setup "$@"
fi

exec "${PROJECT_ROOT}/scripts/start.sh" "$@"

