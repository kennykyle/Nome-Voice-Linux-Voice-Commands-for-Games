"""Action execution for keyboard, mouse, text, delays, and special actions."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows test runners do not provide these modules.
    grp = None
    pwd = None

from .ipc import DaemonClient, DaemonIpcError, DaemonUnavailableError


class InputUnavailableError(RuntimeError):
    pass


INPUT_ACTION_TYPES = {
    "key_press",
    "key_combo",
    "key_hold",
    "key_release",
    "key_toggle",
    "mouse_click",
    "type_text",
}
DAEMON_ACTION_TYPES = INPUT_ACTION_TYPES | {"delay"}
NOME_VOICE_SPECIAL_ACTION_TYPES = {
    "nome_emergency_stop",
    "nome_stop_listening",
}
SYSTEM_ACTION_TYPES = {"system_command"}
SPECIAL_ACTION_TYPES = NOME_VOICE_SPECIAL_ACTION_TYPES | SYSTEM_ACTION_TYPES
SYSTEM_COMMAND_TIMEOUT_MS = 10_000


class ActionRunner:
    def __init__(
        self,
        on_special_action: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._ui = None
        self._ecodes = None
        self._lock = threading.RLock()
        self._held_keys: set[int] = set()
        self._toggled_keys: set[int] = set()
        self._daemon = DaemonClient()
        self._daemon_disabled_until = 0.0
        self._cancel_generation = 0
        self._on_special_action = on_special_action

    def available(self) -> tuple[bool, str]:
        try:
            status = self._daemon.setup_check()
            if status.get("inputAvailable"):
                return True, "Rust daemon input ready"
        except DaemonIpcError:
            pass

        try:
            self._ensure_device()
        except Exception as exc:
            return False, str(exc)
        return True, "uinput ready"

    def execute_async(
        self,
        actions: list[dict[str, Any]],
        on_error: Callable[[str], None] | None = None,
        mode: str = "sequential",
    ) -> None:
        with self._lock:
            generation = self._cancel_generation
        thread = threading.Thread(
            target=self._execute_with_error_handler,
            args=(actions, on_error, generation, mode),
            daemon=True,
        )
        thread.start()

    def stop_all(self) -> None:
        """Cancel app-managed action sequences and release any held virtual inputs."""
        with self._lock:
            self._cancel_generation += 1
        threading.Thread(target=self.release_all, daemon=True).start()

    def release_all(self) -> None:
        self._try_daemon_release_all()

        with self._lock:
            if self._ui is None or self._ecodes is None:
                self._held_keys.clear()
                self._toggled_keys.clear()
                return
            for code in list(self._held_keys):
                self._write_key(code, 0)
            self._held_keys.clear()
            self._toggled_keys.clear()

    def close(self) -> None:
        with self._lock:
            self.release_all()
            if self._ui is not None:
                self._ui.close()
                self._ui = None

    def _execute_with_error_handler(
        self,
        actions: list[dict[str, Any]],
        on_error: Callable[[str], None] | None,
        generation: int,
        mode: str,
    ) -> None:
        try:
            self.execute(actions, generation, mode)
        except Exception as exc:
            if on_error is not None:
                on_error(str(exc))

    def execute(
        self,
        actions: list[dict[str, Any]],
        generation: int | None = None,
        mode: str = "sequential",
    ) -> None:
        if self._cancelled(generation):
            return
        if mode == "parallel":
            self._execute_parallel(actions, generation)
            return
        if self._try_daemon_execute(actions):
            return

        if _actions_require_input_device(actions):
            with self._lock:
                self._ensure_device()
        for action in actions:
            if self._cancelled(generation):
                return
            self._execute_action(action, generation)

    def _execute_parallel(self, actions: list[dict[str, Any]], generation: int | None) -> None:
        if _actions_require_input_device(actions):
            with self._lock:
                self._ensure_device()

        errors: list[Exception] = []

        def run_action(action: dict[str, Any]) -> None:
            if self._cancelled(generation):
                return
            try:
                self._execute_action(action, generation)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run_action, args=(action,), daemon=True)
            for action in actions
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            raise errors[0]

    def _try_daemon_execute(self, actions: list[dict[str, Any]]) -> bool:
        if not all(action.get("type") in DAEMON_ACTION_TYPES for action in actions):
            return False
        if time.monotonic() < self._daemon_disabled_until:
            return False

        try:
            self._daemon.execute_actions(actions)
            return True
        except DaemonUnavailableError:
            self._daemon_disabled_until = time.monotonic() + 3.0
            return False
        except DaemonIpcError as exc:
            if exc.code == "input_unavailable":
                self._daemon_disabled_until = time.monotonic() + 3.0
                return False
            # The daemon got the actions and refused one (for example an unknown key),
            # possibly after running earlier ones. Running them again here would
            # repeat those, so report the error instead.
            raise

    def _try_daemon_release_all(self) -> bool:
        if time.monotonic() < self._daemon_disabled_until:
            return False

        try:
            self._daemon.release_all()
            return True
        except DaemonUnavailableError:
            self._daemon_disabled_until = time.monotonic() + 3.0
            return False
        except DaemonIpcError:
            return False

    def _ensure_device(self) -> None:
        if self._ui is not None:
            return

        try:
            from evdev import UInput, ecodes
        except Exception as exc:
            raise InputUnavailableError("Python package 'evdev' is not available") from exc

        key_codes = _virtual_input_key_codes(ecodes)

        try:
            self._ui = UInput({ecodes.EV_KEY: key_codes}, name="Nome Voice Virtual Input")
        except Exception as exc:
            raise InputUnavailableError(
                f"Could not open /dev/uinput: {exc}. {_uinput_diagnostics()}"
            ) from exc

        self._ecodes = ecodes

    def _execute_action(self, action: dict[str, Any], generation: int | None = None) -> None:
        action_type = action.get("type")

        if action_type == "key_press":
            if not str(action.get("key", "")).strip():
                return
            code = self._key_code(action.get("key", ""))
            duration = float(action.get("durationMs") or action.get("duration_ms") or 30) / 1000.0
            self._write_key(code, 1)
            self._sleep(duration, generation)
            self._write_key(code, 0)
        elif action_type == "key_combo":
            keys = [key for key in action.get("keys", []) if str(key).strip()]
            if len(keys) < 2:
                return
            codes = [self._key_code(key) for key in keys]
            duration = float(action.get("durationMs") or action.get("duration_ms") or 40) / 1000.0
            for code in codes:
                self._write_key(code, 1)
            self._sleep(duration, generation)
            for code in reversed(codes):
                self._write_key(code, 0)
        elif action_type == "key_hold":
            if not str(action.get("key", "")).strip():
                return
            code = self._key_code(action.get("key", ""))
            with self._lock:
                self._write_key(code, 1)
                self._held_keys.add(code)
        elif action_type == "key_release":
            if not str(action.get("key", "")).strip():
                return
            code = self._key_code(action.get("key", ""))
            with self._lock:
                self._write_key(code, 0)
                self._held_keys.discard(code)
                self._toggled_keys.discard(code)
        elif action_type == "key_toggle":
            if not str(action.get("key", "")).strip():
                return
            code = self._key_code(action.get("key", ""))
            with self._lock:
                if code in self._toggled_keys:
                    self._write_key(code, 0)
                    self._toggled_keys.remove(code)
                    self._held_keys.discard(code)
                else:
                    self._write_key(code, 1)
                    self._toggled_keys.add(code)
                    self._held_keys.add(code)
        elif action_type == "mouse_click":
            code = self._mouse_button_code(action.get("button", "left"))
            count = int(action.get("count") or 1)
            for _ in range(max(1, count)):
                if self._cancelled(generation):
                    return
                self._write_key(code, 1)
                self._sleep(0.03, generation)
                self._write_key(code, 0)
                self._sleep(0.03, generation)
        elif action_type == "type_text":
            self._type_text(str(action.get("text", "")), generation)
        elif action_type == "delay":
            ms = int(action.get("ms", 0))
            if ms > 0:
                self._sleep(ms / 1000.0, generation)
        elif action_type in NOME_VOICE_SPECIAL_ACTION_TYPES:
            self._execute_nome_voice_special(action)
        elif action_type == "system_command":
            self._execute_system_command(action, generation)
        else:
            raise ValueError(f"Unsupported action type: {action_type}")

    def _execute_nome_voice_special(self, action: dict[str, Any]) -> None:
        if action.get("type") == "nome_emergency_stop":
            with self._lock:
                self._cancel_generation += 1
        if self._on_special_action is None:
            raise ValueError("Nome Voice special actions need an application handler")
        self._on_special_action(dict(action))

    def _execute_system_command(
        self,
        action: dict[str, Any],
        generation: int | None = None,
    ) -> None:
        command = str(action.get("command", "")).strip()
        if not command:
            return
        if os.name != "posix":
            raise ValueError("System command actions are only available on Linux")
        if self._cancelled(generation):
            return

        timeout_ms = int(action.get("timeoutMs") or SYSTEM_COMMAND_TIMEOUT_MS)
        timeout_seconds = max(0.1, timeout_ms / 1000.0)
        try:
            completed = subprocess.run(
                shlex.split(command),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ValueError(f"System command not found: {exc.filename}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"System command timed out after {timeout_ms} ms") from exc

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "no output").strip()
            raise RuntimeError(
                f"System command failed with exit code {completed.returncode}: {message}"
            )

    def _write_key(self, code: int, value: int) -> None:
        with self._lock:
            if self._ui is None or self._ecodes is None:
                return
            self._ui.write(self._ecodes.EV_KEY, code, value)
            self._ui.syn()

    def _cancelled(self, generation: int | None) -> bool:
        if generation is None:
            return False
        with self._lock:
            return generation != self._cancel_generation

    def _sleep(self, seconds: float, generation: int | None) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._cancelled(generation):
                return False
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        return not self._cancelled(generation)

    def _key_code(self, key: str) -> int:
        ecodes = self._ecodes
        normalized = key.strip().upper().replace("-", "_").replace(" ", "")
        aliases = {
            "CTRL": "LEFTCTRL",
            "CONTROL": "LEFTCTRL",
            "ALT": "LEFTALT",
            "SHIFT": "LEFTSHIFT",
            "SUPER": "LEFTMETA",
            "META": "LEFTMETA",
            "WIN": "LEFTMETA",
            "ESCAPE": "ESC",
            "RETURN": "ENTER",
            "PGUP": "PAGEUP",
            "PGDN": "PAGEDOWN",
        }
        normalized = aliases.get(normalized, normalized)

        if len(normalized) == 1 and normalized.isalpha():
            code_name = f"KEY_{normalized}"
        elif len(normalized) == 1 and normalized.isdigit():
            code_name = f"KEY_{normalized}"
        elif normalized.startswith("KEY_"):
            code_name = normalized
        else:
            code_name = f"KEY_{normalized}"

        if hasattr(ecodes, code_name):
            return int(getattr(ecodes, code_name))
        if code_name in ecodes.ecodes:
            return int(ecodes.ecodes[code_name])

        raise ValueError(f"Unknown key: {key}")

    def _mouse_button_code(self, button: str) -> int:
        ecodes = self._ecodes
        mapping = {
            "left": ecodes.BTN_LEFT,
            "right": ecodes.BTN_RIGHT,
            "middle": ecodes.BTN_MIDDLE,
            "back": ecodes.BTN_SIDE,
            "forward": ecodes.BTN_EXTRA,
        }
        try:
            return int(mapping[button])
        except KeyError as exc:
            raise ValueError(f"Unknown mouse button: {button}") from exc

    def _type_text(self, text: str, generation: int | None = None) -> None:
        for char in text:
            if self._cancelled(generation):
                return
            combo = char_to_keys(char)
            if combo is None:
                continue
            if len(combo) == 1:
                self._tap(self._key_code(combo[0]), generation)
            else:
                modifier = self._key_code(combo[0])
                key = self._key_code(combo[1])
                self._write_key(modifier, 1)
                self._tap(key, generation)
                self._write_key(modifier, 0)
            self._sleep(0.01, generation)

    def _tap(self, code: int, generation: int | None = None) -> None:
        self._write_key(code, 1)
        self._sleep(0.02, generation)
        self._write_key(code, 0)


def _actions_require_input_device(actions: list[dict[str, Any]]) -> bool:
    return any(action.get("type") in INPUT_ACTION_TYPES for action in actions)


def char_to_keys(char: str) -> tuple[str, ...] | None:
    lower = {
        " ": ("SPACE",),
        "\n": ("ENTER",),
        "\t": ("TAB",),
        ".": ("DOT",),
        ",": ("COMMA",),
        "-": ("MINUS",),
        "=": ("EQUAL",),
        "/": ("SLASH",),
        "\\": ("BACKSLASH",),
        ";": ("SEMICOLON",),
        "'": ("APOSTROPHE",),
        "[": ("LEFTBRACE",),
        "]": ("RIGHTBRACE",),
        "`": ("GRAVE",),
    }
    shifted = {
        "!": "1",
        "@": "2",
        "#": "3",
        "$": "4",
        "%": "5",
        "^": "6",
        "&": "7",
        "*": "8",
        "(": "9",
        ")": "0",
        "_": "MINUS",
        "+": "EQUAL",
        "?": "SLASH",
        "|": "BACKSLASH",
        ":": "SEMICOLON",
        '"': "APOSTROPHE",
        "{": "LEFTBRACE",
        "}": "RIGHTBRACE",
        "~": "GRAVE",
        "<": "COMMA",
        ">": "DOT",
    }

    if char.isalpha():
        if char.isupper():
            return ("LEFTSHIFT", char)
        return (char,)
    if char.isdigit():
        return (char,)
    if char in lower:
        return lower[char]
    if char in shifted:
        return ("LEFTSHIFT", shifted[char])
    return None


def _virtual_input_key_codes(ecodes) -> list[int]:
    letters = [f"KEY_{chr(code)}" for code in range(ord("A"), ord("Z") + 1)]
    numbers = [f"KEY_{number}" for number in range(10)]
    function_keys = [f"KEY_F{number}" for number in range(1, 25)]
    keypad = [
        "KEY_KP0",
        "KEY_KP1",
        "KEY_KP2",
        "KEY_KP3",
        "KEY_KP4",
        "KEY_KP5",
        "KEY_KP6",
        "KEY_KP7",
        "KEY_KP8",
        "KEY_KP9",
        "KEY_KPASTERISK",
        "KEY_KPPLUS",
        "KEY_KPMINUS",
        "KEY_KPDOT",
        "KEY_KPSLASH",
        "KEY_KPENTER",
        "KEY_KPEQUAL",
    ]
    common_keys = [
        "KEY_ESC",
        "KEY_1",
        "KEY_2",
        "KEY_3",
        "KEY_4",
        "KEY_5",
        "KEY_6",
        "KEY_7",
        "KEY_8",
        "KEY_9",
        "KEY_0",
        "KEY_MINUS",
        "KEY_EQUAL",
        "KEY_BACKSPACE",
        "KEY_TAB",
        "KEY_LEFTBRACE",
        "KEY_RIGHTBRACE",
        "KEY_ENTER",
        "KEY_LEFTCTRL",
        "KEY_RIGHTCTRL",
        "KEY_SEMICOLON",
        "KEY_APOSTROPHE",
        "KEY_GRAVE",
        "KEY_LEFTSHIFT",
        "KEY_RIGHTSHIFT",
        "KEY_BACKSLASH",
        "KEY_COMMA",
        "KEY_DOT",
        "KEY_SLASH",
        "KEY_LEFTALT",
        "KEY_RIGHTALT",
        "KEY_SPACE",
        "KEY_CAPSLOCK",
        "KEY_NUMLOCK",
        "KEY_SCROLLLOCK",
        "KEY_LEFTMETA",
        "KEY_RIGHTMETA",
        "KEY_UP",
        "KEY_DOWN",
        "KEY_LEFT",
        "KEY_RIGHT",
        "KEY_INSERT",
        "KEY_DELETE",
        "KEY_HOME",
        "KEY_END",
        "KEY_PAGEUP",
        "KEY_PAGEDOWN",
        "KEY_SYSRQ",
        "KEY_PAUSE",
        "KEY_MUTE",
        "KEY_VOLUMEUP",
        "KEY_VOLUMEDOWN",
        "KEY_PLAYPAUSE",
        "KEY_NEXTSONG",
        "KEY_PREVIOUSSONG",
        "BTN_LEFT",
        "BTN_RIGHT",
        "BTN_MIDDLE",
        "BTN_SIDE",
        "BTN_EXTRA",
    ]

    codes: set[int] = set()
    for name in [*letters, *numbers, *function_keys, *keypad, *common_keys]:
        code = getattr(ecodes, name, None)
        if isinstance(code, int) and code > 0:
            codes.add(code)
    return sorted(codes)


def _uinput_diagnostics() -> str:
    path = Path("/dev/uinput")
    if not path.exists():
        return "The uinput device does not exist. Run setup.sh to load the uinput module."

    try:
        device_stat = path.stat()
        mode = stat.filemode(device_stat.st_mode)
        try:
            owner = (
                pwd.getpwuid(device_stat.st_uid).pw_name
                if pwd is not None
                else str(device_stat.st_uid)
            )
        except KeyError:
            owner = str(device_stat.st_uid)
        try:
            group = (
                grp.getgrgid(device_stat.st_gid).gr_name
                if grp is not None
                else str(device_stat.st_gid)
            )
        except KeyError:
            group = str(device_stat.st_gid)
        readable = os.access(path, os.R_OK)
        writable = os.access(path, os.W_OK)
    except Exception as exc:
        return f"Could not inspect /dev/uinput: {exc}"

    return (
        f"/dev/uinput is {mode} {owner}:{group}; "
        f"current user read={readable} write={writable}. "
        "Run setup.sh again so it can install the uinput group rule."
    )
