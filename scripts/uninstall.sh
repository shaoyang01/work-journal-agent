#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

if command -v wj >/dev/null 2>&1; then
  wj uninstall "$@"
else
  PYTHONPATH="${PROJECT_ROOT}/src" python3 -m work_journal_agent uninstall "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  python3 -m pip uninstall -y work-journal-agent || true
fi

