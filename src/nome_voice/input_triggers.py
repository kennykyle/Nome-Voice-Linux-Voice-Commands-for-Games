"""Push-to-talk triggers that work while a game has focus.

* Keyboard key: GNOME's Global Shortcuts portal. No extra permissions; GNOME asks the
  user to confirm the key once, then reports press and release even inside games.
* Mouse button, foot pedal, or other device: read that one device directly. Linux
  only lets the desktop read input devices, so the user grants access to just that
  device once (a udev rule, installed through the normal password prompt).
* On-screen button: handled by the app window itself.
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

TRIGGER_KINDS = ("keyboard", "device", "screen")
DEFAULT_TRIGGER_KIND = "keyboard"
_HEX4 = re.compile(r"[0-9a-f]{4}")
_EVENT_NODE = re.compile(r"event[0-9]+")
_DEVICE_BUSES = {"0003": "USB", "0005": "Bluetooth"}
_LETTER_CODES = {16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 30, 31, 32, 33, 34, 35, 36, 37, 38,
                 44, 45, 46, 47, 48, 49, 50}


@dataclass
class TriggerDevice:
    vendor: str
    product: str
    name: str
    bus: str
    nodes: list[str] = field(default_factory=list)
    can_type: bool = False  # has a full set of letter keys (a keyboard, or a device acting as one)

    @property
    def key(self) -> str:
        return f"{self.vendor}:{self.product}"


def list_trigger_devices(devices_text: str | None = None) -> list[TriggerDevice]:
    """USB and Bluetooth input devices that have keys or buttons, one entry per device."""
    if devices_text is None:
        try:
            devices_text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    grouped: dict[str, TriggerDevice] = {}
    for block in devices_text.strip().split("\n\n"):
        ids = re.search(r"I: Bus=(\w+) Vendor=(\w+) Product=(\w+)", block)
        name = re.search(r'N: Name="(.*)"', block)
        handlers = re.search(r"H: Handlers=(.*)", block)
        keys = re.search(r"B: KEY=(.*)", block)
        if not (ids and name and handlers and keys):
            continue
        bus, vendor, product = (part.lower() for part in ids.groups())
        if bus not in _DEVICE_BUSES or "nome voice" in name.group(1).lower():
            continue
        nodes = [node for node in handlers.group(1).split() if _EVENT_NODE.fullmatch(node)]
        if not nodes:
            continue
        device = grouped.setdefault(
            f"{vendor}:{product}",
            TriggerDevice(vendor=vendor, product=product, name=name.group(1), bus=_DEVICE_BUSES[bus]),
        )
        if len(name.group(1)) < len(device.name):
            device.name = name.group(1)
        device.nodes.extend(nodes)
        codes = _bitmap_codes(keys.group(1))
        if len(codes & _LETTER_CODES) >= 20:
            device.can_type = True
    return sorted(grouped.values(), key=lambda device: device.name.lower())


def find_trigger_device(vendor: str, product: str) -> TriggerDevice | None:
    for device in list_trigger_devices():
        if device.vendor == vendor and device.product == product:
            return device
    return None


def device_readable(device: TriggerDevice) -> bool:
    return any(os.access(f"/dev/input/{node}", os.R_OK) for node in device.nodes)


def code_name(code: int) -> str:
    try:
        from evdev import ecodes
    except Exception:
        return f"code {code}"
    for table in (ecodes.BTN, ecodes.KEY):
        name = table.get(code)
        if name:
            names = name if isinstance(name, (list, tuple)) else [name]
            return str(sorted(names, key=len)[0])
    return f"code {code}"


def grant_device_access(device: TriggerDevice) -> tuple[bool, str]:
    """Install a udev rule so the logged-in user can read this one device.

    Blocks while the system password prompt is open, so call it from a thread.
    """
    if not (_HEX4.fullmatch(device.vendor) and _HEX4.fullmatch(device.product)):
        return False, "Unexpected device ID"
    nodes = [node for node in device.nodes if _EVENT_NODE.fullmatch(node)]
    rule_path = f"/etc/udev/rules.d/70-nome-voice-ptt-{device.vendor}-{device.product}.rules"
    rule = (
        f'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{{id/vendor}}=="{device.vendor}", '
        f'ATTRS{{id/product}}=="{device.product}", TAG+="uaccess"'
    )
    script = "\n".join(
        [
            "set -e",
            f"cat > {rule_path} <<'RULE'",
            "# Nome Voice push-to-talk: let the logged-in user read this one device.",
            rule,
            "RULE",
            "udevadm control --reload-rules",
            "udevadm trigger --action=change " + " ".join(f"/sys/class/input/{node}" for node in nodes),
            "udevadm settle --timeout=5",
        ]
    )
    try:
        completed = subprocess.run(
            ["pkexec", "/bin/sh", "-c", script], capture_output=True, text=True, timeout=300
        )
    except FileNotFoundError:
        return False, "pkexec is not installed"
    except subprocess.TimeoutExpired:
        return False, "The password prompt timed out"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "cancelled"
        return False, f"Access was not granted ({detail})"
    return True, f"Nome Voice can now read {device.name}"


class DeviceButtonListener:
    """Report press/release of one button on one device (all of its input nodes)."""

    def __init__(
        self,
        device: TriggerDevice,
        code: int | None,
        on_press: Callable[[int], None],
        on_release: Callable[[int], None],
        on_status: Callable[[str], None],
        detect_once: bool = False,
    ) -> None:
        self.device = device
        self.code = code
        self._on_press = on_press
        self._on_release = on_release
        self._on_status = on_status
        self._detect_once = detect_once
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.8)
        self._thread = None

    def _run(self) -> None:
        try:
            from evdev import InputDevice, ecodes
        except Exception as exc:
            self._on_status(f"Device push-to-talk unavailable: evdev is missing ({exc})")
            return
        opened = []
        denied = 0
        for node in self.device.nodes:
            try:
                opened.append(InputDevice(f"/dev/input/{node}"))
            except PermissionError:
                denied += 1
            except OSError:
                continue
        if not opened:
            if denied:
                self._on_status(f"Nome Voice needs permission to read {self.device.name}. Use Allow Access.")
            else:
                self._on_status(f"{self.device.name} is not connected")
            return
        if self._detect_once:
            self._on_status(f"Press the button on {self.device.name} you want to use")
        else:
            self._on_status(f"Push-to-talk: {self.device.name}, {code_name(self.code or 0)}")
        try:
            while not self._stop.is_set() and opened:
                try:
                    ready, _write, _error = select.select(opened, [], [], 0.25)
                except OSError:
                    break
                for input_device in ready:
                    try:
                        events = list(input_device.read())
                    except BlockingIOError:
                        continue
                    except OSError:
                        opened.remove(input_device)
                        self._on_status(f"{self.device.name} was disconnected")
                        continue
                    for event in events:
                        if event.type != ecodes.EV_KEY:
                            continue
                        if self._detect_once:
                            if event.value == 1:
                                self._on_press(event.code)
                                return
                            continue
                        if event.code != self.code:
                            continue
                        if event.value == 1:
                            self._on_press(event.code)
                        elif event.value == 0:
                            self._on_release(event.code)
        finally:
            for input_device in opened:
                try:
                    input_device.close()
                except OSError:
                    pass


class PortalPushToTalk:
    """Hold-to-talk on a keyboard key through GNOME's Global Shortcuts portal.

    All calls and callbacks happen on the GTK main thread.
    """

    BUS = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.GlobalShortcuts"
    SHORTCUT_ID = "push-to-talk"

    def __init__(
        self,
        app_id: str,
        preferred_key: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_status: Callable[[str], None],
        on_bound: Callable[[str], None] | None = None,
    ) -> None:
        self.app_id = app_id
        self.preferred_key = preferred_key
        self._on_press = on_press
        self._on_release = on_release
        self._on_status = on_status
        self._on_bound = on_bound
        self._bus = None
        self._session: str | None = None
        self._signal_ids: list[int] = []
        self._pending_bind = False
        self.trigger_description = ""

    def start(self, bind: bool = True) -> None:
        from gi.repository import Gio, GLib

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            self._on_status(f"GNOME shortcut push-to-talk unavailable: {exc.message}")
            return
        self._register_app()
        for signal_name in ("Activated", "Deactivated"):
            self._signal_ids.append(
                self._bus.signal_subscribe(
                    self.BUS, self.IFACE, signal_name, self.PATH, None,
                    Gio.DBusSignalFlags.NONE, self._on_shortcut_signal,
                )
            )
        self._pending_bind = bind
        session_token = "nomevoice" + uuid.uuid4().hex[:12]
        self._request(
            "CreateSession",
            lambda token: GLib.Variant(
                "(a{sv})",
                ({"handle_token": GLib.Variant("s", token),
                  "session_handle_token": GLib.Variant("s", session_token)},),
            ),
            self._on_session_created,
        )

    def configure(self) -> None:
        """Open GNOME's dialog to change the key."""
        from gi.repository import Gio, GLib

        if self._bus is None or self._session is None:
            self._on_status("GNOME shortcut session is not ready yet")
            return
        self._bus.call(
            self.BUS, self.PATH, self.IFACE, "ConfigureShortcuts",
            GLib.Variant("(osa{sv})", (self._session, "", {})),
            None, Gio.DBusCallFlags.NONE, -1, None, self._on_call_done,
        )

    def stop(self) -> None:
        from gi.repository import Gio

        if self._bus is None:
            return
        for signal_id in self._signal_ids:
            self._bus.signal_unsubscribe(signal_id)
        self._signal_ids.clear()
        if self._session:
            self._bus.call(
                self.BUS, self._session, "org.freedesktop.portal.Session", "Close",
                None, None, Gio.DBusCallFlags.NONE, -1, None, None,
            )
        self._session = None

    def _register_app(self) -> None:
        from gi.repository import Gio, GLib

        try:
            self._bus.call_sync(
                self.BUS, self.PATH, "org.freedesktop.host.portal.Registry", "Register",
                GLib.Variant("(sa{sv})", (self.app_id, {})),
                None, Gio.DBusCallFlags.NONE, 2000, None,
            )
        except GLib.Error:
            pass  # older portal, or this connection already registered

    def _request(self, method: str, build_params, on_response) -> None:
        from gi.repository import Gio

        token = "nomevoice" + uuid.uuid4().hex[:12]
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        subscription = {"id": 0}

        def handle_response(_conn, _sender, _path, _iface, _signal, parameters, *_user):
            self._bus.signal_unsubscribe(subscription["id"])
            response, results = parameters.unpack()
            on_response(response, results)

        subscription["id"] = self._bus.signal_subscribe(
            self.BUS, "org.freedesktop.portal.Request", "Response", request_path, None,
            Gio.DBusSignalFlags.NONE, handle_response,
        )
        self._bus.call(
            self.BUS, self.PATH, self.IFACE, method, build_params(token),
            None, Gio.DBusCallFlags.NONE, -1, None, self._on_call_done,
        )

    def _on_call_done(self, bus, result) -> None:
        from gi.repository import GLib

        try:
            bus.call_finish(result)
        except GLib.Error as exc:
            self._on_status(f"GNOME shortcut request failed: {exc.message}")

    def _on_session_created(self, response: int, results: dict) -> None:
        if response != 0 or "session_handle" not in results:
            self._on_status("GNOME shortcut session was refused")
            return
        self._session = str(results["session_handle"])
        if self._pending_bind:
            self._bind()

    def _bind(self) -> None:
        from gi.repository import GLib

        shortcut = {
            "description": GLib.Variant("s", "Nome Voice push-to-talk (hold to talk)"),
            "preferred_trigger": GLib.Variant("s", self.preferred_key or "F13"),
        }
        self._request(
            "BindShortcuts",
            lambda token: GLib.Variant(
                "(oa(sa{sv})sa{sv})",
                (self._session, [(self.SHORTCUT_ID, shortcut)], "",
                 {"handle_token": GLib.Variant("s", token)}),
            ),
            self._on_bound_response,
        )

    def _on_bound_response(self, response: int, results: dict) -> None:
        if response != 0:
            self._on_status("Push-to-talk key was not set up (cancelled in GNOME)")
            return
        for shortcut_id, info in results.get("shortcuts", []):
            if shortcut_id == self.SHORTCUT_ID:
                self.trigger_description = str(info.get("trigger_description", "")) or "set"
        self._on_status(f"Push-to-talk key: {self.trigger_description or 'set'} (works in games)")
        if self._on_bound is not None:
            self._on_bound(self.trigger_description)

    def _on_shortcut_signal(self, _conn, _sender, _path, _iface, signal_name, parameters, *_user) -> None:
        session, shortcut_id, _timestamp, _options = parameters.unpack()
        if session != self._session or shortcut_id != self.SHORTCUT_ID:
            return
        if signal_name == "Activated":
            self._on_press()
        else:
            self._on_release()


def _bitmap_codes(bitmap: str) -> set[int]:
    """Decode a /proc/bus/input/devices capability bitmap ("B: KEY=...")."""
    value = 0
    for word in bitmap.split():
        value = (value << 64) | int(word, 16)
    return {bit for bit in range(value.bit_length()) if value >> bit & 1}
