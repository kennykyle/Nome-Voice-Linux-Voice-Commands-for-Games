#!/usr/bin/env bash
# Set up (or remove) the echo-cancelled microphone for Nome Voice.
#
#   bash scripts/setup-echo-cancel.sh          # install and start
#   bash scripts/setup-echo-cancel.sh --remove # stop and remove
#
# It runs as its own small PipeWire client, so your normal audio is not restarted
# and other apps keep using the regular microphone. Afterwards, pick
# "Microphone, echo cancelled (Nome Voice)" in Nome Voice > Settings > Setup.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}"
CONF="${CONFIG_DIR}/nome-voice/echo-cancel.conf"
UNIT_DIR="${CONFIG_DIR}/systemd/user"
UNIT="nome-voice-echo-cancel.service"

if [[ "${1:-}" == "--remove" ]]; then
  systemctl --user disable --now "${UNIT}" 2>/dev/null || true
  rm -f "${UNIT_DIR}/${UNIT}" "${CONF}"
  systemctl --user daemon-reload
  echo "Echo cancelling removed."
  exit 0
fi

if [[ ! -e /usr/lib/spa-0.2/aec/libspa-aec-webrtc.so ]]; then
  echo "PipeWire's WebRTC echo canceller is missing (package: pipewire)." >&2
  exit 1
fi

install -Dm644 "${ROOT}/packaging/pipewire/nome-voice-echo-cancel.conf" "${CONF}"
install -Dm644 "${ROOT}/packaging/systemd/${UNIT}" "${UNIT_DIR}/${UNIT}"
systemctl --user daemon-reload
systemctl --user enable --now "${UNIT}"
echo "Echo cancelling is on. In Nome Voice, choose:"
echo "  Settings > Setup > Microphone > Microphone, echo cancelled (Nome Voice)"
