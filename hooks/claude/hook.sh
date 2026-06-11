#!/usr/bin/env bash
set -euo pipefail

# Usage in Claude Code settings:
#   "command": "/path/to/work-journal-agent/hooks/claude/hook.sh UserPromptSubmit"
#
# The hook JSON is read from stdin and passed to the installed `wj` command.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EVENT_TYPE="${1:-}"

if command -v wj >/dev/null 2>&1; then
  if [[ -n "${EVENT_TYPE}" ]]; then
    exec wj claude-hook --event-type "${EVENT_TYPE}"
  fi
  exec wj claude-hook
fi

if command -v python3 >/dev/null 2>&1; then
  if [[ -n "${EVENT_TYPE}" ]]; then
    exec env PYTHONPATH="${PROJECT_ROOT}/src" python3 -m work_journal_agent claude-hook --event-type "${EVENT_TYPE}"
  fi
  exec env PYTHONPATH="${PROJECT_ROOT}/src" python3 -m work_journal_agent claude-hook
fi

if command -v python >/dev/null 2>&1; then
  if [[ -n "${EVENT_TYPE}" ]]; then
    exec env PYTHONPATH="${PROJECT_ROOT}/src" python -m work_journal_agent claude-hook --event-type "${EVENT_TYPE}"
  fi
  exec env PYTHONPATH="${PROJECT_ROOT}/src" python -m work_journal_agent claude-hook
fi

echo "Python 3.11+ or installed wj command is required." >&2
exit 1
