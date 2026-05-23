"""Small Unix-socket client for nome-voiced.

The GTK views should use this module instead of knowing protocol details.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DaemonIpcError(RuntimeError):
    """Raised when nome-voiced returns an error response or cannot be reached."""


@dataclass(frozen=True)
class DaemonResponse:
    ok: bool
    result: dict[str, Any] | None
    error: dict[str, Any] | None


class DaemonClient:
    def __init__(self, socket_path: Path | None = None) -> None:
        self.socket_path = socket_path or default_socket_path()

    def status(self) -> dict[str, Any]:
        return self.request("status.get")

    def setup_check(self) -> dict[str, Any]:
        return self.request("setup.check")

    def set_listening(self, listening: bool) -> dict[str, Any]:
        return self.request("listening.set", {"listening": listening})

    def activate_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        return self.request("profile.activate", {"profile": profile})

    def test_command(self, text: str) -> dict[str, Any]:
        return self.request("command.test", {"text": text})

    def execute_actions(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request("actions.execute", {"actions": actions})

    def release_all(self) -> dict[str, Any]:
        return self.request("actions.releaseAll")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not hasattr(socket, "AF_UNIX"):
            raise DaemonIpcError("Unix sockets are not available on this platform")

        request_id = str(uuid.uuid4())
        payload = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(str(self.socket_path))
                connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                line = _read_line(connection)
        except OSError as exc:
            raise DaemonIpcError(
                f"Could not connect to nome-voiced at {self.socket_path}: {exc}"
            ) from exc

        response = _parse_response(line, request_id)
        if not response.ok:
            error = response.error or {}
            code = error.get("code", "daemon_error")
            message = error.get("message", "nome-voiced returned an error")
            raise DaemonIpcError(f"{code}: {message}")

        return response.result or {}


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        base = Path(runtime_dir)
    else:
        base = Path(tempfile.gettempdir())
    return base / "nome-voice" / "nome-voiced.sock"


def _read_line(connection: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break

    if not chunks:
        raise DaemonIpcError("nome-voiced closed the connection without a response")

    return b"".join(chunks).split(b"\n", 1)[0]


def _parse_response(line: bytes, expected_id: str) -> DaemonResponse:
    try:
        decoded = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DaemonIpcError(f"nome-voiced returned invalid JSON: {exc}") from exc

    if decoded.get("id") != expected_id:
        raise DaemonIpcError("nome-voiced response id did not match the request")

    return DaemonResponse(
        ok=bool(decoded.get("ok")),
        result=decoded.get("result"),
        error=decoded.get("error"),
    )
