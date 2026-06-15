#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${HOME}/Applications/Work Journal Agent.app"

"${ROOT_DIR}/scripts/build-local-app.sh" "${APP_DIR}"
open "${APP_DIR}"
echo "Installed ${APP_DIR}"
