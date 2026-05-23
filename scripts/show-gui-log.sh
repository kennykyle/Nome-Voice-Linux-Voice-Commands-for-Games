#!/usr/bin/env bash
set -euo pipefail

LOG_PATH="${XDG_CACHE_HOME:-${HOME}/.cache}/nome-voice/nome-voice.log"

if [[ ! -f "${LOG_PATH}" ]]; then
  echo "No Nome Voice GUI log found at ${LOG_PATH}"
  exit 1
fi

tail -n 200 "${LOG_PATH}"
