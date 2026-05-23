#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

python - "${ROOT}" <<'PY'
import json
import os
import socket
import sys
import tempfile
import uuid
from pathlib import Path

root = Path(sys.argv[1])
socket_path = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "nome-voice" / "nome-voiced.sock"
profile_path = root / "examples" / "profile.example.json"


def send(method, params=None):
    request_id = str(uuid.uuid4())
    payload = {
        "id": request_id,
        "method": method,
        "params": params or {},
    }

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        response = connection.recv(65536).split(b"\n", 1)[0]

    decoded = json.loads(response.decode("utf-8"))
    print(f"\n{method}")
    print(json.dumps(decoded, indent=2))
    return decoded


if not socket_path.exists():
    raise SystemExit(
        "nome-voiced is not running at "
        f"{socket_path}\n\n"
        "Run setup first so it can build and start the daemon:\n"
        "  bash setup.sh --skip-model\n\n"
        "Or start it manually:\n"
        "  ~/.local/bin/nome-voiced\n"
    )

with profile_path.open("r", encoding="utf-8") as handle:
    profile = json.load(handle)

send("status.get")
send("profile.activate", {"profile": profile})
send("listening.set", {"listening": True})
send("command.test", {"text": "reload"})
send("command.test", {"text": "open map"})
send("actions.releaseAll")
PY
