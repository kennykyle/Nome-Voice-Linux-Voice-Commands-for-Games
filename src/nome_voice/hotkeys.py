"""Global hotkey listener backed by evdev when available."""

from __future__ import annotations

import select
import threading
from collections.abc import Callable
from typing import Any


class GlobalHotkeyListener:
    def __init__(
        self,
        hotkey: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_status: Callable[[str], None],
    ) -> None:
        self.hotkey = hotkey
        self._on_press = on_press
        self._on_release = on_release
        self._on_status = on_status
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.8)
            self._thread = None

    def _run(self) -> None:
        try:
            from evdev import InputDevice, ecodes, list_devices
        except Exception as exc:
            self._on_status(f"Global hotkey unavailable: evdev is not available ({exc})")
            return

        try:
            hotkey_code = _evdev_key_code(self.hotkey, ecodes)
        except Exception as exc:
            self._on_status(f"Global hotkey unavailable: {exc}")
            return

        devices: list[Any] = []
        denied = 0
        for path in list_devices():
            try:
                device = InputDevice(path)
                capabilities = device.capabilities()
                key_codes = capabilities.get(ecodes.EV_KEY, [])
                if _looks_like_keyboard(key_codes, ecodes, hotkey_code):
                    devices.append(device)
            except PermissionError:
                denied += 1
            except OSError:
                continue

        if not devices:
            if denied:
                self._on_status("Global hotkey needs keyboard read access. Focused-window hotkey fallback is active.")
            else:
                self._on_status("Global hotkey did not find a keyboard. Focused-window hotkey fallback is active.")
            return

        self._on_status(f"Global hotkey active: {self.hotkey}")
        while not self._stop_event.is_set():
            try:
                ready, _write, _error = select.select(devices, [], [], 0.25)
            except OSError:
                break
            for device in ready:
                try:
                    for event in device.read():
                        if event.type != ecodes.EV_KEY or event.code != hotkey_code:
                            continue
                        if event.value == 1:
                            self._on_press()
                        elif event.value == 0:
                            self._on_release()
                except OSError:
                    if device in devices:
                        devices.remove(device)

        for device in devices:
            try:
                device.close()
            except OSError:
                pass


def _looks_like_keyboard(key_codes: list[int], ecodes: Any, hotkey_code: int) -> bool:
    code_set = {int(code) for code in key_codes if isinstance(code, int)}
    if hotkey_code in code_set:
        return True

    common_names = [
        "KEY_A",
        "KEY_B",
        "KEY_C",
        "KEY_Z",
        "KEY_1",
        "KEY_0",
        "KEY_SPACE",
        "KEY_ENTER",
        "KEY_ESC",
        "KEY_TAB",
    ]
    common_codes = [getattr(ecodes, name, None) for name in common_names]
    common_hits = sum(1 for code in common_codes if code in code_set)
    if common_hits >= 4:
        return True

    letter_names = [f"KEY_{chr(code)}" for code in range(ord("A"), ord("Z") + 1)]
    letter_hits = sum(1 for name in letter_names if getattr(ecodes, name, None) in code_set)
    function_hits = sum(1 for index in range(1, 13) if getattr(ecodes, f"KEY_F{index}", None) in code_set)
    return letter_hits >= 10 or (letter_hits >= 3 and function_hits >= 3)


def _evdev_key_code(key: str, ecodes: Any) -> int:
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
    code_name = normalized if normalized.startswith("KEY_") else f"KEY_{normalized}"
    if hasattr(ecodes, code_name):
        return int(getattr(ecodes, code_name))
    if code_name in ecodes.ecodes:
        return int(ecodes.ecodes[code_name])
    raise ValueError(f"unknown hotkey: {key}")
