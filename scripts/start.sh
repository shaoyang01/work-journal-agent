#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

if command -v wj >/dev/null 2>&1; then
  exec wj setup "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec env PYTHONPATH="${PROJECT_ROOT}/src" python3 -m work_journal_agent setup "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec env PYTHONPATH="${PROJECT_ROOT}/src" python -m work_journal_agent setup "$@"
fi

echo "Python 3.11+ is required but was not found." >&2
exit 1

