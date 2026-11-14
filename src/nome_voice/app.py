"""GTK application for Nome Voice."""

from __future__ import annotations

import copy
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from .actions import ActionRunner
from .hotkeys import GlobalHotkeyListener
from .matcher import command_phrases, normalize_phrase
from .paths import default_model_path
from .profile_store import (
    DEFAULT_SPEECH_SETTINGS,
    delete_profile,
    default_settings,
    export_profile,
    import_profile,
    load_profiles,
    load_settings,
    save_profile,
    save_settings,
    validate_profile,
)
from .recognizer import MicLevelMonitor, VoiceRecognizer, list_microphones
from .recognition import (
    best_final_match as scored_best_final_match,
    result_confidence as scored_result_confidence,
    result_text as scored_result_text,
)


APP_ID = "local.nomevoice.NomeVoice"
APP_NAME = "Nome Voice"
APP_ICON_NAME = "nome-voice"
DESKTOP_FILE_ID = f"{APP_ID}.desktop"

INPUT_ACTION_TYPES = [
    "key_press",
    "key_combo",
    "key_hold",
    "key_release",
    "key_toggle",
    "mouse_click",
    "type_text",
    "delay",
]
NOME_VOICE_ACTION_TYPES = [
    "nome_emergency_stop",
    "nome_stop_listening",
]
SYSTEM_ACTION_TYPES = [
    "system_command",
]
SPECIAL_ACTION_TYPES = NOME_VOICE_ACTION_TYPES + SYSTEM_ACTION_TYPES
ACTION_TYPES = INPUT_ACTION_TYPES + SPECIAL_ACTION_TYPES
ACTION_CATEGORY_ORDER = ["input", "nome_voice", "system"]
ACTION_CATEGORY_LABELS = {
    "input": "Input",
    "nome_voice": "Nome Voice Actions",
    "system": "System Actions",
}
ACTION_CATEGORY_TYPES = {
    "input": INPUT_ACTION_TYPES,
    "nome_voice": NOME_VOICE_ACTION_TYPES,
    "system": SYSTEM_ACTION_TYPES,
}
ACTION_TYPE_LABELS = {
    "key_press": "Key Press",
    "key_combo": "Key Combo",
    "key_hold": "Key Hold",
    "key_release": "Key Release",
    "key_toggle": "Key Toggle",
    "mouse_click": "Mouse Click",
    "type_text": "Type Text",
    "delay": "Delay",
    "nome_emergency_stop": "Emergency Stop",
    "nome_stop_listening": "Pause Listening",
    "system_command": "Run Command",
}
SYSTEM_COMMAND_PRESETS = [
    ("Power Off", "systemctl poweroff"),
    ("Restart", "systemctl reboot"),
]

RESUME_IGNORE_SECONDS = 0.35
LOG_MAX_LINES = 1500
RESPONSE_SPEED_OPTIONS = [
    ("fast", "Fast - about 0.15 s after you stop"),
    ("balanced", "Balanced - about 0.25 s (recommended)"),
    ("patient", "Patient - about 0.4 s, allows pauses inside phrases"),
]
TRIGGER_GUARD_OPTIONS = [
    ("off", "Off"),
    ("standard", "Standard - ignores word forms like 'reloading'"),
    ("strong", "Strong - also ignores everyday words (recommended)"),
]
MIC_LEVEL_CSS = """
button.emergency-stop {
  font-size: 20px;
  font-weight: 700;
  padding: 18px;
}

box.action-row {
  border: 1px solid alpha(currentColor, 0.18);
  border-radius: 8px;
  padding: 10px;
  background-color: alpha(currentColor, 0.04);
}

button.action-icon {
  min-width: 34px;
  min-height: 34px;
  padding: 4px;
}

progressbar.mic-level-active trough progress {
  background-color: #2f7d4f;
}

progressbar.mic-level-paused trough progress {
  background-color: #4f6fa8;
}
"""
AUTOSTART_DESKTOP_FILE = DESKTOP_FILE_ID
LEGACY_DESKTOP_FILES = (
    "nome-voice.desktop",
    "vox-action.desktop",
    "vox-actions.desktop",
    "voxaction.desktop",
)
AUTOSTART_DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Nome Voice
Comment=Voice commands for games
Exec={exec_command}
Icon={icon_name}
Terminal=false
Categories=Utility;Accessibility;
StartupWMClass={app_id}
X-GNOME-Autostart-enabled=true
"""

KEY_CHOICES = (
    [chr(code) for code in range(ord("A"), ord("Z") + 1)]
    + [str(number) for number in range(0, 10)]
    + [f"F{number}" for number in range(1, 25)]
    + [
        "LEFTCTRL",
        "RIGHTCTRL",
        "LEFTSHIFT",
        "RIGHTSHIFT",
        "LEFTALT",
        "RIGHTALT",
        "LEFTMETA",
        "RIGHTMETA",
        "SPACE",
        "ENTER",
        "TAB",
        "ESC",
        "BACKSPACE",
        "CAPSLOCK",
        "NUMLOCK",
        "SCROLLLOCK",
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "HOME",
        "END",
        "PAGEUP",
        "PAGEDOWN",
        "INSERT",
        "DELETE",
        "SYSRQ",
        "PAUSE",
        "MINUS",
        "EQUAL",
        "LEFTBRACE",
        "RIGHTBRACE",
        "BACKSLASH",
        "SEMICOLON",
        "APOSTROPHE",
        "GRAVE",
        "COMMA",
        "DOT",
        "SLASH",
        "KP0",
        "KP1",
        "KP2",
        "KP3",
        "KP4",
        "KP5",
        "KP6",
        "KP7",
        "KP8",
        "KP9",
        "KPDOT",
        "KPPLUS",
        "KPMINUS",
        "KPSLASH",
        "KPASTERISK",
        "KPEQUAL",
        "KPENTER",
        "MUTE",
        "VOLUMEDOWN",
        "VOLUMEUP",
        "PLAYPAUSE",
        "PREVIOUSSONG",
        "NEXTSONG",
    ]
)

MOUSE_BUTTON_CHOICES = ["left", "right", "middle", "back", "forward"]


def _action_category(action_type: str) -> str:
    if action_type in NOME_VOICE_ACTION_TYPES:
        return "nome_voice"
    if action_type in SYSTEM_ACTION_TYPES:
        return "system"
    return "input"


def _action_label(action_type: str) -> str:
    return ACTION_TYPE_LABELS.get(action_type, action_type.replace("_", " ").title())


def _append_choices(combo: Gtk.ComboBoxText, choices: list[str] | tuple[str, ...]) -> None:
    for choice in choices:
        combo.append(choice, choice)


def _set_combo_text(combo: Gtk.ComboBoxText, value: str, choices: list[str] | tuple[str, ...]) -> None:
    text = value.strip().upper()
    if not text:
        combo.set_active(-1)
        return
    if text not in choices:
        combo.append(text, text)
    combo.set_active_id(text)


def _find_key_choice(text: str) -> str | None:
    query = text.strip().upper().replace("-", "").replace("_", "").replace(" ", "")
    if not query:
        return None
    normalized_choices = [
        (choice, choice.replace("_", ""))
        for choice in KEY_CHOICES
    ]
    for choice, normalized in normalized_choices:
        if normalized == query:
            return choice
    for choice, normalized in normalized_choices:
        if normalized.startswith(query):
            return choice
    for choice, normalized in normalized_choices:
        if query in normalized:
            return choice
    return None


def _key_name_from_keyval(keyval: int) -> str | None:
    raw_name = Gdk.keyval_name(keyval)
    if raw_name is None:
        return None
    normalized = raw_name.upper().replace("-", "_")
    aliases = {
        "RETURN": "ENTER",
        "ISO_ENTER": "ENTER",
        "LINEFEED": "ENTER",
        "ESCAPE": "ESC",
        "CONTROL_L": "LEFTCTRL",
        "CONTROL_R": "RIGHTCTRL",
        "SHIFT_L": "LEFTSHIFT",
        "SHIFT_R": "RIGHTSHIFT",
        "ALT_L": "LEFTALT",
        "ALT_R": "RIGHTALT",
        "SUPER_L": "LEFTMETA",
        "SUPER_R": "RIGHTMETA",
        "META_L": "LEFTMETA",
        "META_R": "RIGHTMETA",
        "PAGE_UP": "PAGEUP",
        "PAGE_DOWN": "PAGEDOWN",
        "KP_DECIMAL": "KPDOT",
        "KP_ADD": "KPPLUS",
        "KP_SUBTRACT": "KPMINUS",
        "KP_DIVIDE": "KPSLASH",
        "KP_MULTIPLY": "KPASTERISK",
        "KP_EQUAL": "KPEQUAL",
        "KP_ENTER": "KPENTER",
        "KP_INSERT": "KP0",
        "KP_END": "KP1",
        "KP_DOWN": "KP2",
        "KP_PAGE_DOWN": "KP3",
        "KP_LEFT": "KP4",
        "KP_BEGIN": "KP5",
        "KP_RIGHT": "KP6",
        "KP_HOME": "KP7",
        "KP_UP": "KP8",
        "KP_PAGE_UP": "KP9",
        "KP_DELETE": "KPDOT",
        "BRACKETLEFT": "LEFTBRACE",
        "BRACKETRIGHT": "RIGHTBRACE",
        "PERIOD": "DOT",
        "PRINT": "SYSRQ",
        "ESC": "ESC",
        "ISO_LEFT_TAB": "TAB",
    }
    if normalized.startswith("KP_") and normalized[3:].isdigit():
        return f"KP{normalized[3:]}"
    if len(normalized) == 1 and normalized.isalnum():
        return normalized
    return aliases.get(normalized, normalized if normalized in KEY_CHOICES else None)


def _field(label: str, child: Gtk.Widget) -> Gtk.Widget:
    _detach_widget(child)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label_widget = Gtk.Label(label=label)
    label_widget.set_xalign(0)
    box.append(label_widget)
    box.append(child)
    return box


def _icon_button(icon_name: str, tooltip: str) -> Gtk.Button:
    button = Gtk.Button()
    button.set_child(Gtk.Image.new_from_icon_name(icon_name))
    button.set_tooltip_text(tooltip)
    button.add_css_class("action-icon")
    return button


def _detach_widget(widget: Gtk.Widget) -> None:
    parent = widget.get_parent()
    if parent is not None and hasattr(parent, "remove"):
        parent.remove(widget)


def _optional_positive_int(text: str) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _nonnegative_int(text: str) -> int:
    try:
        return max(0, int(text.strip()))
    except ValueError:
        return 0


def _clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _model_path_from_value(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def _command_action_mode(command: dict[str, Any]) -> str:
    return "parallel" if command.get("actionExecution") == "parallel" else "sequential"


class NomeVoiceApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = MainWindow(self)
        window.present()


class KeyPicker:
    def __init__(
        self,
        choices: list[str] | tuple[str, ...],
        tooltip: str,
        on_changed: Callable[["KeyPicker"], None],
    ) -> None:
        self.choices = list(choices)
        self._active_id: str | None = None
        self._on_changed = on_changed
        self.button = Gtk.MenuButton()
        self.button.set_tooltip_text(tooltip)
        self._label = Gtk.Label(label="Select Key")
        self.button.set_child(self._label)

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.button.add_controller(key_controller)

        popover = Gtk.Popover()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.set_margin_top(8)
        panel.set_margin_bottom(8)
        panel.set_margin_start(8)
        panel.set_margin_end(8)

        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Search keys")
        self.search_entry.connect("changed", lambda _entry: self._filter_rows())
        panel.append(self.search_entry)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        for choice in self.choices:
            self._append_row(choice)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.listbox)
        scrolled.set_min_content_height(220)
        scrolled.set_min_content_width(220)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        panel.append(scrolled)
        popover.set_child(panel)
        self.button.set_popover(popover)

    def append(self, key: str, label: str | None = None) -> None:
        key = key.strip().upper()
        if not key or key in self.choices:
            return
        self.choices.append(key)
        self._append_row(label or key)

    def get_active_id(self) -> str | None:
        return self._active_id

    def set_active_id(self, key: str) -> None:
        key = key.strip().upper()
        if not key:
            self.set_active(-1)
            return
        if key not in self.choices:
            self.append(key)
        self._active_id = key
        self._label.set_text(key)
        self._on_changed(self)

    def set_active(self, index: int) -> None:
        if index < 0:
            self._active_id = None
            self._label.set_text("Select Key")
            self._on_changed(self)
            return
        if index < len(self.choices):
            self.set_active_id(self.choices[index])

    def _append_row(self, key: str) -> None:
        row = Gtk.ListBoxRow()
        row.key_id = key  # type: ignore[attr-defined]
        label = Gtk.Label(label=key)
        label.set_xalign(0)
        label.set_margin_top(4)
        label.set_margin_bottom(4)
        label.set_margin_start(6)
        label.set_margin_end(6)
        row.set_child(label)
        self.listbox.append(row)

    def _filter_rows(self) -> None:
        query = (
            self.search_entry.get_text()
            .strip()
            .upper()
            .replace("-", "")
            .replace("_", "")
            .replace(" ", "")
        )
        child = self.listbox.get_first_child()
        while child is not None:
            key = str(getattr(child, "key_id", ""))
            normalized = key.replace("_", "")
            child.set_visible(not query or query in normalized)
            child = child.get_next_sibling()

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        key = str(getattr(row, "key_id", ""))
        if key:
            self.set_active_id(key)
            self.button.popdown()

    def _on_key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        key_name = _key_name_from_keyval(keyval)
        if key_name is None:
            return False
        self.set_active_id(key_name)
        return True


class ActionRow:
    def __init__(
        self,
        action: dict[str, Any] | None,
        on_remove,
        on_type_changed,
        on_capture_key: Callable[[Callable[[str], None], Gtk.Button | None], None],
        on_move,
        on_copy,
        on_paste,
    ) -> None:
        self._on_changed = on_type_changed
        self._on_capture_key = on_capture_key
        self._on_move = on_move
        self._on_copy = on_copy
        self._on_paste = on_paste
        self._suppress_combo_picker_change = False
        self._suppress_action_type_change = False
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.box.add_css_class("action-row")
        self.box.set_margin_top(8)
        self.box.set_margin_bottom(8)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_row.set_valign(Gtk.Align.CENTER)

        self.drag_handle = Gtk.Image.new_from_icon_name("open-menu-symbolic")
        self.drag_handle.set_tooltip_text("Drag this action row to reorder it.")
        top_row.append(self.drag_handle)

        self.category_combo = Gtk.ComboBoxText()
        for category in ACTION_CATEGORY_ORDER:
            self.category_combo.append(category, ACTION_CATEGORY_LABELS[category])
        self.category_combo.set_tooltip_text("Action category for this step.")
        self.category_combo.connect(
            "changed",
            lambda _combo: self._on_action_category_changed(),
        )
        top_row.append(_field("Category", self.category_combo))

        self.type_combo = Gtk.ComboBoxText()
        self.type_combo.set_tooltip_text("Action type for this step.")
        self.type_combo.connect("changed", lambda _combo: self._on_action_type_changed())
        top_row.append(_field("Action Type", self.type_combo))

        tool_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tool_row.set_valign(Gtk.Align.END)
        self.move_up_button = _icon_button("go-up-symbolic", "Move this action earlier.")
        self.move_up_button.connect("clicked", lambda _button: on_move(self, -1))
        self.move_down_button = _icon_button("go-down-symbolic", "Move this action later.")
        self.move_down_button.connect("clicked", lambda _button: on_move(self, 1))
        self.copy_button = _icon_button("edit-copy-symbolic", "Copy this action.")
        self.copy_button.connect("clicked", lambda _button: on_copy(self))
        self.paste_button = _icon_button("edit-paste-symbolic", "Paste copied action after this one.")
        self.paste_button.connect("clicked", lambda _button: on_paste(self))
        self.remove_button = _icon_button("user-trash-symbolic", "Remove this action.")
        self.remove_button.connect("clicked", lambda _button: on_remove(self))
        tool_row.append(self.move_up_button)
        tool_row.append(self.move_down_button)
        tool_row.append(self.copy_button)
        tool_row.append(self.paste_button)
        tool_row.append(self.remove_button)
        top_row.append(tool_row)
        self.box.append(top_row)

        self.fields_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.fields_box.set_hexpand(True)
        self.box.append(self.fields_box)

        self.key_combo = KeyPicker(
            KEY_CHOICES,
            "Choose a keyboard key, search keys, or focus this button and press a key.",
            lambda _picker: self._on_changed(self),
        )

        self.key_capture_button = Gtk.Button(label="Press Key")
        self.key_capture_button.set_tooltip_text("Press a key on the keyboard to assign it here.")
        self.key_capture_button.connect("clicked", lambda _button: self._capture_key_for_single_key())

        self.value_entry = Gtk.Entry()
        self.value_entry.set_hexpand(True)
        self.value_entry.connect("changed", lambda _entry: self._on_changed(self))

        self.extra_entry = Gtk.Entry()
        self.extra_entry.set_hexpand(True)
        self.extra_entry.connect("changed", lambda _entry: self._on_changed(self))

        self.combo_key_picker = KeyPicker(
            KEY_CHOICES,
            "Choose a key to add to the combo, search keys, or focus this button and press a key.",
            self._on_combo_key_picker_changed,
        )

        self.combo_capture_button = Gtk.Button(label="Press Key")
        self.combo_capture_button.connect("clicked", lambda _button: self._capture_key_for_combo())
        clear_combo_button = Gtk.Button(label="Clear")
        clear_combo_button.connect("clicked", lambda _button: self._clear_combo_keys())
        combo_button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        combo_button_row.append(self.combo_capture_button)
        combo_button_row.append(clear_combo_button)
        self.combo_key_tools = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.combo_key_tools.append(self.combo_key_picker.button)
        self.combo_key_tools.append(combo_button_row)

        self.mouse_combo = Gtk.ComboBoxText()
        _append_choices(self.mouse_combo, MOUSE_BUTTON_CHOICES)
        self.mouse_combo.set_tooltip_text("Choose which mouse button to click.")
        self.mouse_combo.connect("changed", lambda _combo: self._on_changed(self))

        self.system_preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, command in SYSTEM_COMMAND_PRESETS:
            preset_button = Gtk.Button(label=label)
            preset_button.set_tooltip_text(f"Use: {command}")
            preset_button.connect(
                "clicked",
                lambda _button, preset_command=command: self._set_system_command(preset_command),
            )
            self.system_preset_row.append(preset_button)

        self.set_action(action or {"type": "key_press", "key": "R"})

    def set_action(self, action: dict[str, Any]) -> None:
        action_type = action.get("type", "key_press")
        if action_type not in ACTION_TYPES:
            action_type = "key_press"
        self._suppress_action_type_change = True
        self.category_combo.set_active_id(_action_category(action_type))
        self._populate_type_combo(action_type)
        self._suppress_action_type_change = False
        if action_type == "key_combo":
            self.value_entry.set_text(", ".join(action.get("keys", [])))
            self.extra_entry.set_text(str(action.get("durationMs") or ""))
        elif action_type == "mouse_click":
            self.mouse_combo.set_active_id(str(action.get("button", "left")).lower())
            self.extra_entry.set_text(str(action.get("count") or ""))
        elif action_type == "type_text":
            self.value_entry.set_text(str(action.get("text", "")))
            self.extra_entry.set_text("")
        elif action_type == "delay":
            self.value_entry.set_text(str(action.get("ms", 250)))
            self.extra_entry.set_text("")
        elif action_type == "system_command":
            self.value_entry.set_text(str(action.get("command", "")))
            self.extra_entry.set_text(str(action.get("timeoutMs") or ""))
        elif action_type in {"nome_emergency_stop", "nome_stop_listening"}:
            self.value_entry.set_text("")
            self.extra_entry.set_text("")
        elif action_type == "key_press":
            _set_combo_text(self.key_combo, str(action.get("key", "")), KEY_CHOICES)
            self.extra_entry.set_text(str(action.get("durationMs") or ""))
        else:
            _set_combo_text(self.key_combo, str(action.get("key", "")), KEY_CHOICES)
            self.extra_entry.set_text("")
        self._refresh_fields()

    def to_action(self) -> dict[str, Any]:
        action_type = self.type_combo.get_active_id() or "key_press"
        value = self.value_entry.get_text().strip()
        extra = self.extra_entry.get_text().strip()

        if action_type == "key_press":
            action: dict[str, Any] = {
                "type": action_type,
                "key": self.key_combo.get_active_id() or "",
            }
            duration = _optional_positive_int(extra)
            if duration is not None:
                action["durationMs"] = duration
            return action
        if action_type == "key_combo":
            action = {
                "type": action_type,
                "keys": [key.strip() for key in value.split(",") if key.strip()],
            }
            duration = _optional_positive_int(extra)
            if duration is not None:
                action["durationMs"] = duration
            return action
        if action_type in {"key_hold", "key_release", "key_toggle"}:
            return {"type": action_type, "key": self.key_combo.get_active_id() or ""}
        if action_type == "mouse_click":
            action = {"type": action_type, "button": self.mouse_combo.get_active_id() or "left"}
            count = _optional_positive_int(extra)
            if count is not None:
                action["count"] = count
            return action
        if action_type == "type_text":
            return {"type": action_type, "text": value}
        if action_type == "delay":
            return {"type": action_type, "ms": _nonnegative_int(value)}
        if action_type in {"nome_emergency_stop", "nome_stop_listening"}:
            return {"type": action_type}
        if action_type == "system_command":
            action = {"type": action_type, "command": value}
            timeout = _optional_positive_int(extra)
            if timeout is not None:
                action["timeoutMs"] = timeout
            return action
        raise ValueError(f"Unsupported action type: {action_type}")

    def set_order_state(self, index: int, total: int) -> None:
        self.move_up_button.set_sensitive(index > 0)
        self.move_down_button.set_sensitive(index < total - 1)

    def _populate_type_combo(self, active_action_type: str | None = None) -> None:
        category = self.category_combo.get_active_id() or "input"
        action_types = ACTION_CATEGORY_TYPES.get(category, INPUT_ACTION_TYPES)
        self.type_combo.remove_all()
        for action_type in action_types:
            self.type_combo.append(action_type, _action_label(action_type))
        if active_action_type in action_types:
            self.type_combo.set_active_id(active_action_type)
        else:
            self.type_combo.set_active_id(action_types[0])

    def _on_action_category_changed(self) -> None:
        if self._suppress_action_type_change:
            return
        self._suppress_action_type_change = True
        self._populate_type_combo()
        self._suppress_action_type_change = False
        self._refresh_fields()
        self._on_changed(self)

    def _on_action_type_changed(self) -> None:
        if self._suppress_action_type_change:
            return
        self._refresh_fields()
        self._on_changed(self)

    def _refresh_fields(self) -> None:
        action_type = self.type_combo.get_active_id() or "key_press"
        child = self.fields_box.get_first_child()
        while child is not None:
            self.fields_box.remove(child)
            child = self.fields_box.get_first_child()

        if action_type == "key_press":
            self.extra_entry.set_placeholder_text("30")
            self.fields_box.append(self._key_field("Key"))
            self.fields_box.append(_field("Hold Time (ms)", self.extra_entry))
        elif action_type in {"key_hold", "key_release", "key_toggle"}:
            self.fields_box.append(self._key_field("Key"))
        elif action_type == "key_combo":
            self.value_entry.set_placeholder_text("Example: LEFTCTRL, M")
            self.extra_entry.set_placeholder_text("40")
            self.fields_box.append(_field("Keys In Order", self.value_entry))
            self.fields_box.append(_field("Key Menu", self.combo_key_tools))
            self.fields_box.append(_field("Hold Time (ms)", self.extra_entry))
        elif action_type == "mouse_click":
            self.extra_entry.set_placeholder_text("1")
            self.fields_box.append(_field("Mouse Button", self.mouse_combo))
            self.fields_box.append(_field("Click Count", self.extra_entry))
        elif action_type == "type_text":
            self.value_entry.set_placeholder_text("Text to type")
            self.fields_box.append(_field("Typed Text", self.value_entry))
        elif action_type == "delay":
            self.value_entry.set_placeholder_text("250")
            self.fields_box.append(_field("Delay (ms)", self.value_entry))
        elif action_type == "system_command":
            self.value_entry.set_placeholder_text("systemctl poweroff")
            self.extra_entry.set_placeholder_text("10000")
            self.fields_box.append(_field("Command", self.value_entry))
            self.fields_box.append(_field("Presets", self.system_preset_row))
            self.fields_box.append(_field("Timeout (ms)", self.extra_entry))

    def _set_system_command(self, command: str) -> None:
        self.value_entry.set_text(command)
        if not self.extra_entry.get_text().strip():
            self.extra_entry.set_text("10000")
        self._on_changed(self)

    def _key_field(self, label: str) -> Gtk.Widget:
        _detach_widget(self.key_combo.button)
        _detach_widget(self.key_capture_button)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.append(self.key_combo.button)
        row.append(self.key_capture_button)
        return _field(label, row)

    def _on_key_combo_key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        return self._set_single_key_from_keyval(keyval)

    def _on_combo_key_picker_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        key_name = _key_name_from_keyval(keyval)
        if key_name is None:
            return False
        if self.combo_key_picker.get_active_id() == key_name:
            self._append_combo_key(key_name)
        else:
            _set_combo_text(self.combo_key_picker, key_name, KEY_CHOICES)
        return True

    def _on_combo_key_picker_changed(self, _combo: Any) -> None:
        if self._suppress_combo_picker_change:
            return
        self._append_combo_key(self.combo_key_picker.get_active_id())

    def _set_single_key_from_keyval(self, keyval: int) -> bool:
        key_name = _key_name_from_keyval(keyval)
        if key_name is None:
            return False
        _set_combo_text(self.key_combo, key_name, KEY_CHOICES)
        self._on_changed(self)
        return True

    def _capture_key_for_single_key(self) -> None:
        self._on_capture_key(lambda key_name: self._set_single_key(key_name), self.key_capture_button)

    def _capture_key_for_combo(self) -> None:
        self._on_capture_key(lambda key_name: self._append_combo_key(key_name), self.combo_capture_button)

    def _set_single_key(self, key_name: str) -> None:
        _set_combo_text(self.key_combo, key_name, KEY_CHOICES)
        self._on_changed(self)

    def _append_combo_key(self, key_name: str | None = None) -> None:
        selected = key_name or self.combo_key_picker.get_active_id()
        if not selected:
            return
        existing = [key.strip() for key in self.value_entry.get_text().split(",") if key.strip()]
        if selected not in existing:
            existing.append(selected)
        self.value_entry.set_text(", ".join(existing))
        self._on_changed(self)

    def _clear_combo_keys(self) -> None:
        self._suppress_combo_picker_change = True
        self.combo_key_picker.set_active(-1)
        self._suppress_combo_picker_change = False
        self.value_entry.set_text("")
        self._on_changed(self)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: NomeVoiceApplication) -> None:
        super().__init__(application=app)
        self.set_title(APP_NAME)
        if hasattr(self, "set_icon_name"):
            self.set_icon_name(APP_ICON_NAME)
        self.set_default_size(1320, 820)
        self.set_size_request(1100, 700)

        self.settings = load_settings()
        self.profiles = load_profiles()
        self.current_profile_index = 0
        self.current_command_index = 0
        self.action_rows: list[ActionRow] = []
        self._action_clipboard: dict[str, Any] | None = None
        self._dragged_action_row: ActionRow | None = None
        self.recognizer: VoiceRecognizer | None = None
        self.mic_monitor: MicLevelMonitor | None = None
        self._mic_monitor_signature: tuple[Any, ...] | None = None
        self._mic_monitor_failure_logged = False
        self.action_runner = ActionRunner(
            on_special_action=lambda action: GLib.idle_add(self._handle_special_action, action)
        )
        self.listening = False
        self._closing = False
        self._recognizer_signature: tuple[Any, ...] | None = None
        self._last_mic_level = 0.0
        self._command_cooldowns: dict[str, float] = {}
        self._ignore_recognition_until = 0.0
        self._recognition_delay_until = 0.0
        self._loading_ui = False
        self._loading_microphones = False
        self._autosave_source_id: int | None = None
        self._last_autosave_error: str | None = None
        self._key_capture_callback: Callable[[str], None] | None = None
        self._key_capture_button: Gtk.Button | None = None
        self._key_capture_button_label = ""
        self._global_hotkey_listener: GlobalHotkeyListener | None = None
        self._focused_hotkey_down = False
        self._hotkey_status = "Focused-window fallback active"
        self.phrase_rows: list[Gtk.Entry] = []

        self._build_ui()
        self._load_profile_list()
        self._load_profile_into_ui(self.profiles[0])
        self._refresh_setup_status()
        if self.settings.get("startOnLogin"):
            self._apply_start_on_login_setting(silent=True)
        self._start_mic_monitor()

        if not self.settings.get("firstRunComplete"):
            GLib.idle_add(self._show_first_run_dialog)
        elif self.settings.get("startListeningOnLaunch"):
            GLib.idle_add(self._start_listening_on_launch)

    def _build_ui(self) -> None:
        self._install_css()
        header = Gtk.HeaderBar()
        title = Adw.WindowTitle(title=APP_NAME)
        header.set_title_widget(title)
        settings_button = Gtk.Button()
        settings_button.set_child(Gtk.Image.new_from_icon_name("emblem-system-symbolic"))
        settings_button.set_tooltip_text("Settings")
        settings_button.connect("clicked", lambda _button: self._show_settings())
        header.pack_end(settings_button)
        self.set_titlebar(header)

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_window_key_pressed)
        key_controller.connect("key-released", self._on_window_key_released)
        self.add_controller(key_controller)

        self.root_stack = Gtk.Stack()
        self.root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.set_child(self.root_stack)

        main_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_homogeneous(False)
        body.set_vexpand(True)
        main_root.append(body)

        body.append(self._build_sidebar())
        body.append(self._build_main_area())
        main_root.append(self._build_mic_meter())

        self.root_stack.add_named(main_root, "main")
        self.root_stack.add_named(self._build_settings_screen(), "settings")
        self.root_stack.set_visible_child_name("main")

    def _install_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(MIC_LEVEL_CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_sidebar(self) -> Gtk.Widget:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar.set_size_request(340, -1)
        sidebar.set_hexpand(False)
        sidebar.set_vexpand(True)

        status_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.status_button = Gtk.Button()
        self.status_button.add_css_class("suggested-action")
        self.status_button.set_size_request(-1, 92)
        self.status_button.connect("clicked", self._on_status_clicked)
        status_panel.append(self.status_button)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self._on_status_pressed)
        gesture.connect("released", self._on_status_released)
        self.status_button.add_controller(gesture)

        self.emergency_stop_button = Gtk.Button(label="Emergency Stop")
        self.emergency_stop_button.add_css_class("destructive-action")
        self.emergency_stop_button.add_css_class("emergency-stop")
        self.emergency_stop_button.set_size_request(-1, 76)
        self.emergency_stop_button.set_tooltip_text(
            "Stop listening, cancel running command actions, and release held inputs."
        )
        self.emergency_stop_button.connect("clicked", self._emergency_stop)
        status_panel.append(self.emergency_stop_button)

        self.last_heard_label = Gtk.Label(label="Last heard: -")
        self.last_heard_label.set_xalign(0)
        self.last_heard_label.set_wrap(True)
        status_panel.append(self.last_heard_label)

        self.last_command_label = Gtk.Label(label="Last command fired: -")
        self.last_command_label.set_xalign(0)
        self.last_command_label.set_wrap(True)
        status_panel.append(self.last_command_label)
        sidebar.append(self._section("Status", status_panel))

        profile_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.profile_combo = Gtk.ComboBoxText()
        self.profile_combo.connect("changed", self._on_profile_changed)
        profile_panel.append(self.profile_combo)

        profile_name_label = Gtk.Label(label="Profile Name")
        profile_name_label.set_xalign(0)
        profile_panel.append(profile_name_label)

        self.profile_name_entry = Gtk.Entry()
        self.profile_name_entry.set_placeholder_text("Profile name")
        self.profile_name_entry.connect("changed", lambda _entry: self._queue_autosave())
        profile_panel.append(self.profile_name_entry)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_button = Gtk.Button(label="New")
        new_button.connect("clicked", self._new_profile)
        duplicate_button = Gtk.Button(label="Duplicate")
        duplicate_button.connect("clicked", self._duplicate_profile)
        row.append(new_button)
        row.append(duplicate_button)
        profile_panel.append(row)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        import_button = Gtk.Button(label="Import")
        import_button.connect("clicked", self._import_profile_dialog)
        export_button = Gtk.Button(label="Export")
        export_button.connect("clicked", self._export_profile_dialog)
        row.append(import_button)
        row.append(export_button)
        profile_panel.append(row)

        delete_button = Gtk.Button(label="Delete Profile")
        delete_button.connect("clicked", self._delete_current_profile)
        profile_panel.append(delete_button)
        sidebar.append(self._section("Profile", profile_panel))

        activation_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mode_label = Gtk.Label(label="Activation Mode")
        mode_label.set_xalign(0)
        activation_panel.append(mode_label)

        self.activation_combo = Gtk.ComboBoxText()
        for mode, text in [
            ("always", "Always Listening"),
            ("push_to_talk", "Push To Talk"),
        ]:
            self.activation_combo.append(mode, text)
        self.activation_combo.connect("changed", self._on_activation_changed)
        activation_panel.append(self.activation_combo)

        hotkey_label = Gtk.Label(label="Global Hotkey")
        hotkey_label.set_xalign(0)
        activation_panel.append(hotkey_label)

        hotkey_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.hotkey_combo = Gtk.ComboBoxText()
        _append_choices(self.hotkey_combo, KEY_CHOICES)
        self.hotkey_combo.set_tooltip_text("Choose a global hotkey, or focus this menu and press a key.")
        self.hotkey_combo.connect("changed", self._on_hotkey_changed)
        hotkey_controller = Gtk.EventControllerKey()
        hotkey_controller.connect("key-pressed", self._on_hotkey_combo_key_pressed)
        self.hotkey_combo.add_controller(hotkey_controller)
        self.hotkey_capture_button = Gtk.Button(label="Press Key")
        self.hotkey_capture_button.connect(
            "clicked",
            lambda _button: self._capture_next_key(self._set_hotkey, self.hotkey_capture_button),
        )
        hotkey_row.append(self.hotkey_combo)
        hotkey_row.append(self.hotkey_capture_button)
        activation_panel.append(hotkey_row)
        sidebar.append(self._section("Activation", activation_panel))

        self._refresh_status_button()
        return sidebar

    def _build_main_area(self) -> Gtk.Widget:
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main.set_hexpand(True)
        main.set_vexpand(True)

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_titled(self._build_commands_page(), "commands", "Commands")
        self.stack.add_titled(self._build_log_page(), "log", "Log")

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_halign(Gtk.Align.START)
        self.commands_nav_button = Gtk.Button(label="Commands")
        self.commands_nav_button.connect("clicked", lambda _button: self._show_page("commands"))
        self.log_nav_button = Gtk.Button(label="Log")
        self.log_nav_button.connect("clicked", lambda _button: self._show_page("log"))
        nav.append(self.commands_nav_button)
        nav.append(self.log_nav_button)
        main.append(nav)
        main.append(self.stack)
        self._refresh_nav_buttons()
        return main

    def _build_settings_screen(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(16)
        root.set_margin_end(16)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_button = Gtk.Button(label="Back")
        back_button.connect("clicked", lambda _button: self._show_main())
        title = Gtk.Label(label="Settings")
        title.add_css_class("title-2")
        title.set_hexpand(True)
        title.set_xalign(0)
        header.append(back_button)
        header.append(title)
        root.append(header)

        self.settings_stack = Gtk.Stack()
        self.settings_stack.set_hexpand(True)
        self.settings_stack.set_vexpand(True)
        self.settings_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.settings_stack.add_titled(self._build_general_settings_page(), "general", "General")
        self.settings_stack.add_titled(self._build_setup_settings_page(), "setup", "Setup")

        settings_tabs = Gtk.StackSwitcher()
        settings_tabs.set_stack(self.settings_stack)
        settings_tabs.set_halign(Gtk.Align.CENTER)
        root.append(settings_tabs)
        root.append(self.settings_stack)
        return root

    def _build_general_settings_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(4)
        page.set_margin_bottom(4)

        recognition_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        recognition_panel.append(self._build_final_recognition_panel())
        page.append(self._section("Recognition", recognition_panel))

        startup_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        self.start_listening_on_launch_switch = Gtk.Switch()
        self.start_listening_on_launch_switch.set_valign(Gtk.Align.CENTER)
        self.start_listening_on_launch_switch.set_active(
            bool(self.settings.get("startListeningOnLaunch", False))
        )
        self.start_listening_on_launch_switch.connect(
            "notify::active",
            self._on_start_listening_on_launch_changed,
        )
        startup_panel.append(
            self._setting_row(
                "Start Listening When Opened",
                "Begin recognition automatically after Nome Voice finishes loading.",
                self.start_listening_on_launch_switch,
            )
        )

        self.start_on_login_switch = Gtk.Switch()
        self.start_on_login_switch.set_valign(Gtk.Align.CENTER)
        self.start_on_login_switch.set_active(bool(self.settings.get("startOnLogin", False)))
        self.start_on_login_switch.connect("notify::active", self._on_start_on_login_changed)
        startup_panel.append(
            self._setting_row(
                "Start App On System Startup",
                "Create a GNOME autostart entry for your user account.",
                self.start_on_login_switch,
            )
        )
        page.append(self._section("Startup", startup_panel))

        defaults_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        reset_button = Gtk.Button(label="Reset Settings To Defaults")
        reset_button.set_halign(Gtk.Align.START)
        reset_button.set_tooltip_text(
            "Reset recognition, setup, activation, and command tuning without changing phrases or actions."
        )
        reset_button.connect("clicked", self._confirm_reset_settings_to_defaults)
        defaults_panel.append(reset_button)
        page.append(self._section("Defaults", defaults_panel))

        return self._scrolled(page)

    def _build_final_recognition_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        title = Gtk.Label(label="Final Recognition")
        title.set_xalign(0)
        title.add_css_class("heading")
        panel.append(title)

        summary = Gtk.Label(
            label=(
                "Commands fire when a complete phrase is heard. Response Speed sets how "
                "quickly that happens; the guard keeps everyday talk from firing commands."
            )
        )
        summary.set_xalign(0)
        summary.set_wrap(True)
        summary.add_css_class("dim-label")
        panel.append(summary)

        self.response_speed_combo = Gtk.ComboBoxText()
        for speed_id, label in RESPONSE_SPEED_OPTIONS:
            self.response_speed_combo.append(speed_id, label)
        self.response_speed_combo.set_tooltip_text(
            "How long Nome Voice waits after you finish a command before pressing keys."
        )
        self.response_speed_combo.connect(
            "changed",
            lambda combo: self._on_speech_choice_changed(combo, "responseSpeed"),
        )
        panel.append(
            self._setting_row(
                "Response Speed",
                "Faster fires sooner, but can also fire if you pause right after a command "
                "word in the middle of a sentence. Patient waits for slow or paused speech.",
                self.response_speed_combo,
            )
        )

        self.trigger_guard_combo = Gtk.ComboBoxText()
        for guard_id, label in TRIGGER_GUARD_OPTIONS:
            self.trigger_guard_combo.append(guard_id, label)
        self.trigger_guard_combo.set_tooltip_text(
            "Lets the recognizer hear ordinary words so they don't get mistaken for commands."
        )
        self.trigger_guard_combo.connect(
            "changed",
            lambda combo: self._on_speech_choice_changed(combo, "triggerGuard"),
        )
        panel.append(
            self._setting_row(
                "Accidental Trigger Guard",
                "Reduces commands firing while you talk, watch videos, or chat. "
                "If real commands start getting missed, try Standard.",
                self.trigger_guard_combo,
            )
        )

        self.min_confidence_spin = Gtk.SpinButton.new_with_range(0.0, 1.0, 0.01)
        self.min_confidence_spin.set_digits(2)
        self.min_confidence_spin.set_width_chars(6)
        self.min_confidence_spin.set_tooltip_text(
            "Minimum final Vosk confidence required before a command can fire."
        )
        self.min_confidence_spin.connect(
            "value-changed",
            lambda spin: self._on_speech_float_changed(spin, "minConfidence"),
        )
        panel.append(
            self._setting_row(
                "Minimum Confidence",
                "Final command matches below this confidence are rejected.",
                self.min_confidence_spin,
            )
        )

        self.command_cooldown_spin = Gtk.SpinButton.new_with_range(0, 5000, 50)
        self.command_cooldown_spin.set_width_chars(7)
        self.command_cooldown_spin.set_tooltip_text(
            "Milliseconds before the same voice command can fire again."
        )
        self.command_cooldown_spin.connect(
            "value-changed",
            lambda spin: self._on_speech_integer_changed(spin, "commandCooldownMs"),
        )
        panel.append(
            self._setting_row(
                "Command Cooldown",
                "Prevents double-fires while still allowing quick repeated commands.",
                self.command_cooldown_spin,
            )
        )

        self.min_unrecognized_confidence_spin = Gtk.SpinButton.new_with_range(0.0, 1.0, 0.01)
        self.min_unrecognized_confidence_spin.set_digits(2)
        self.min_unrecognized_confidence_spin.set_width_chars(6)
        self.min_unrecognized_confidence_spin.set_tooltip_text(
            "Only log unrecognized final speech at or above this confidence."
        )
        self.min_unrecognized_confidence_spin.connect(
            "value-changed",
            lambda spin: self._on_speech_float_changed(spin, "minUnrecognizedConfidence"),
        )
        panel.append(
            self._setting_row(
                "Minimum Unrecognized Confidence",
                "Filters low-confidence non-command speech in the log.",
                self.min_unrecognized_confidence_spin,
            )
        )

        self.show_confidence_switch = Gtk.Switch()
        self.show_confidence_switch.set_valign(Gtk.Align.CENTER)
        self.show_confidence_switch.connect(
            "notify::active",
            lambda switch, _param: self._on_speech_boolean_changed(switch, "showConfidence"),
        )
        panel.append(
            self._setting_row(
                "Show Confidence Level",
                "Display final confidence next to the last heard phrase.",
                self.show_confidence_switch,
            )
        )

        self.recognized_delay_spin = Gtk.SpinButton.new_with_range(0, 5000, 25)
        self.recognized_delay_spin.set_width_chars(7)
        self.recognized_delay_spin.set_tooltip_text(
            "Milliseconds to suppress new command execution after a command fires."
        )
        self.recognized_delay_spin.connect(
            "value-changed",
            lambda spin: self._on_speech_integer_changed(spin, "recognizedDelayMs"),
        )
        panel.append(
            self._setting_row(
                "Recognized Speech Delay",
                "Suppresses additional final command execution after a command fires.",
                self.recognized_delay_spin,
            )
        )

        self.unrecognized_delay_spin = Gtk.SpinButton.new_with_range(0, 5000, 25)
        self.unrecognized_delay_spin.set_width_chars(7)
        self.unrecognized_delay_spin.set_tooltip_text(
            "Milliseconds to suppress command execution after no command fires."
        )
        self.unrecognized_delay_spin.connect(
            "value-changed",
            lambda spin: self._on_speech_integer_changed(spin, "unrecognizedDelayMs"),
        )
        panel.append(
            self._setting_row(
                "Unrecognized Speech Delay",
                "Suppresses final command execution after speech is rejected or unmatched.",
                self.unrecognized_delay_spin,
            )
        )

        self.max_alternatives_spin = Gtk.SpinButton.new_with_range(0, 10, 1)
        self.max_alternatives_spin.set_width_chars(5)
        self.max_alternatives_spin.set_tooltip_text(
            "How many final Vosk alternatives to evaluate. Use 0 for fastest calibrated confidence."
        )
        self.max_alternatives_spin.connect(
            "value-changed",
            lambda spin: self._on_speech_integer_changed(spin, "maxAlternatives"),
        )
        panel.append(
            self._setting_row(
                "Max Alternatives",
                "Evaluates multiple final guesses. Zero is fastest and keeps word confidence calibrated.",
                self.max_alternatives_spin,
            )
        )

        return panel

    def _setting_row(self, title: str, description: str, control: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        description_label = Gtk.Label(label=description)
        description_label.set_xalign(0)
        description_label.set_wrap(True)
        description_label.add_css_class("dim-label")
        text.append(label)
        text.append(description_label)
        row.append(text)
        row.append(control)
        return row

    def _build_setup_settings_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(4)
        page.set_margin_bottom(4)

        checks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.setup_status_label = Gtk.Label(label="")
        self.setup_status_label.set_xalign(0)
        self.setup_status_label.set_wrap(True)
        checks_panel.append(self.setup_status_label)
        page.append(self._section("System Checks", checks_panel))

        speech_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        engine_label = Gtk.Label(label="Recognition Engine")
        engine_label.set_xalign(0)
        speech_panel.append(engine_label)

        self.speech_engine_label = Gtk.Label(label="Vosk Speech Recognition")
        self.speech_engine_label.set_xalign(0)
        self.speech_engine_label.add_css_class("dim-label")
        speech_panel.append(self.speech_engine_label)

        model_label = Gtk.Label(label="Speech Model Path")
        model_label.set_xalign(0)
        speech_panel.append(model_label)

        self.model_entry = Gtk.Entry()
        self.model_entry.set_text(str(self._settings_model_path()))
        speech_panel.append(self.model_entry)

        mic_label = Gtk.Label(label="Microphone")
        mic_label.set_xalign(0)
        speech_panel.append(mic_label)

        self.microphone_combo = Gtk.ComboBoxText()
        self.microphone_combo.connect("changed", self._on_microphone_changed)
        speech_panel.append(self.microphone_combo)
        self._load_microphones()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh_button = Gtk.Button(label="Refresh Checks")
        refresh_button.connect("clicked", lambda _button: self._refresh_setup_status())
        refresh_mics_button = Gtk.Button(label="Refresh Microphones")
        refresh_mics_button.connect("clicked", self._refresh_microphones)
        save_setup_button = Gtk.Button(label="Save Setup")
        save_setup_button.connect("clicked", self._save_setup)
        row.append(refresh_button)
        row.append(refresh_mics_button)
        row.append(save_setup_button)
        speech_panel.append(row)
        page.append(self._section("Speech Input", speech_panel))

        test_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        test_label = Gtk.Label(label="Recognition Test Phrase")
        test_label.set_xalign(0)
        test_panel.append(test_label)

        self.test_phrase_entry = Gtk.Entry()
        self.test_phrase_entry.set_text("reload")
        test_panel.append(self.test_phrase_entry)

        test_button = Gtk.Button(label="Test Recognition With This Phrase")
        test_button.connect("clicked", self._test_recognition)
        test_panel.append(test_button)
        page.append(self._section("Recognition Test", test_panel))

        return self._scrolled(page)

    def _build_commands_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        page.set_margin_top(12)
        page.set_margin_bottom(12)
        page.set_margin_start(12)
        page.set_margin_end(12)

        command_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        command_panel.set_size_request(300, -1)
        command_panel.set_hexpand(False)

        self.command_list = Gtk.ListBox()
        self.command_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.command_list.connect("row-selected", self._on_command_row_selected)
        command_panel.append(self._scrolled(self.command_list))

        command_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_command_button = Gtk.Button(label="Add Command")
        add_command_button.connect("clicked", self._add_command)
        duplicate_command_button = Gtk.Button(label="Duplicate")
        duplicate_command_button.connect("clicked", self._duplicate_command)
        delete_command_button = Gtk.Button(label="Delete Command")
        delete_command_button.connect("clicked", self._delete_command)
        command_buttons.append(add_command_button)
        command_buttons.append(duplicate_command_button)
        command_buttons.append(delete_command_button)
        command_panel.append(command_buttons)
        command_section = self._section("Commands", command_panel)
        command_section.set_hexpand(False)
        page.append(command_section)

        detail_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        detail_panel.set_hexpand(True)

        command_info_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name_label = Gtk.Label(label="Command Name")
        name_label.set_xalign(0)
        command_info_panel.append(name_label)

        self.command_name_entry = Gtk.Entry()
        self.command_name_entry.set_tooltip_text("Visible name for this command in Nome Voice.")
        self.command_name_entry.connect("changed", self._on_command_name_changed)
        command_info_panel.append(self.command_name_entry)

        min_confidence_label = Gtk.Label(label="Command Minimum Confidence Override")
        min_confidence_label.set_xalign(0)
        command_info_panel.append(min_confidence_label)

        command_min_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.command_min_confidence_switch = Gtk.Switch()
        self.command_min_confidence_switch.set_valign(Gtk.Align.CENTER)
        self.command_min_confidence_switch.set_tooltip_text(
            "Use a command-specific confidence threshold instead of the profile default."
        )
        self.command_min_confidence_switch.connect(
            "notify::active",
            self._on_command_min_confidence_override_changed,
        )
        self.command_min_confidence_spin = Gtk.SpinButton.new_with_range(0.0, 1.0, 0.01)
        self.command_min_confidence_spin.set_digits(2)
        self.command_min_confidence_spin.set_width_chars(6)
        self.command_min_confidence_spin.set_tooltip_text(
            "Optional final Vosk confidence threshold for this command."
        )
        self.command_min_confidence_spin.connect(
            "value-changed",
            lambda _spin: None if self._loading_ui else self._queue_autosave(),
        )
        command_min_row.append(self.command_min_confidence_switch)
        command_min_row.append(self.command_min_confidence_spin)
        command_info_panel.append(command_min_row)

        command_weight_label = Gtk.Label(label="Command Weight")
        command_weight_label.set_xalign(0)
        command_info_panel.append(command_weight_label)

        self.command_weight_spin = Gtk.SpinButton.new_with_range(0.0, 1.0, 0.01)
        self.command_weight_spin.set_digits(2)
        self.command_weight_spin.set_width_chars(6)
        self.command_weight_spin.set_tooltip_text(
            "App-side confidence bonus added after Vosk scores a matching phrase."
        )
        self.command_weight_spin.connect(
            "value-changed",
            lambda _spin: None if self._loading_ui else self._queue_autosave(),
        )
        command_info_panel.append(self.command_weight_spin)
        detail_panel.append(self._section("Command Details", command_info_panel))

        phrases_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.phrases_list = Gtk.ListBox()
        self.phrases_list.set_selection_mode(Gtk.SelectionMode.NONE)
        phrases_panel.append(self._scrolled(self.phrases_list))

        phrase_add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.new_phrase_entry = Gtk.Entry()
        self.new_phrase_entry.set_placeholder_text("Say this phrase")
        self.new_phrase_entry.set_tooltip_text(
            "Add one spoken phrase or a recognition alias for this command."
        )
        add_phrase_button = Gtk.Button(label="Add Phrase")
        add_phrase_button.connect("clicked", self._add_phrase_from_entry)
        phrase_add_row.append(self.new_phrase_entry)
        phrase_add_row.append(add_phrase_button)
        phrases_panel.append(phrase_add_row)
        detail_panel.append(self._section("Spoken Phrases", phrases_panel))

        actions_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        actions_label = Gtk.Label(label="Action Sequence")
        actions_label.set_xalign(0)
        actions_panel.append(actions_label)

        self.parallel_actions_switch = Gtk.Switch()
        self.parallel_actions_switch.set_valign(Gtk.Align.CENTER)
        self.parallel_actions_switch.connect(
            "notify::active",
            lambda _switch, _param: None if self._loading_ui else self._queue_autosave(),
        )
        actions_panel.append(
            self._setting_row(
                "Run Actions At The Same Time",
                "Off runs actions sequentially. On starts every action row together.",
                self.parallel_actions_switch,
            )
        )

        self.actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        actions_panel.append(self.actions_box)

        action_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_action_button = Gtk.Button(label="Add Action")
        add_action_button.connect("clicked", self._add_action)
        action_buttons.append(add_action_button)
        paste_action_button = Gtk.Button(label="Paste Action")
        paste_action_button.set_tooltip_text("Paste the copied action at the end of this command.")
        paste_action_button.connect("clicked", lambda _button: self._paste_action_at_end())
        action_buttons.append(paste_action_button)

        test_button = Gtk.Button(label="Run This Command Now")
        test_button.set_tooltip_text("Runs this action sequence immediately without using voice.")
        test_button.connect("clicked", self._run_selected_command)
        action_buttons.append(test_button)
        actions_panel.append(action_buttons)
        detail_panel.append(self._section("Actions", actions_panel))
        page.append(detail_panel)

        return self._scrolled(page)

    def _build_log_page(self) -> Gtk.Widget:
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_margin_top(12)
        page.set_margin_bottom(12)
        page.set_margin_start(12)
        page.set_margin_end(12)
        page.append(self._scrolled(self.log_view))
        return page

    def _build_mic_meter(self) -> Gtk.Widget:
        meter = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        meter.set_margin_start(12)
        meter.set_margin_end(12)
        meter.set_margin_bottom(10)

        label = Gtk.Label(label="Mic Level")
        label.set_xalign(0)
        meter.append(label)

        self.mic_level_bar = Gtk.ProgressBar()
        self.mic_level_bar.set_hexpand(True)
        self.mic_level_bar.set_fraction(0.0)
        self.mic_level_bar.add_css_class("mic-level-paused")
        self.mic_level_bar.set_tooltip_text(
            "Microphone level. Blue means paused monitoring; green means active recognition."
        )
        meter.append(self.mic_level_bar)

        self.mic_level_label = Gtk.Label(label="0%")
        self.mic_level_label.set_width_chars(4)
        self.mic_level_label.set_xalign(1)
        meter.append(self.mic_level_label)

        return meter

    def _section(self, title: str, child: Gtk.Widget) -> Gtk.Frame:
        child.set_margin_top(10)
        child.set_margin_bottom(10)
        child.set_margin_start(10)
        child.set_margin_end(10)

        frame = Gtk.Frame()
        frame.set_label(title)
        frame.set_child(child)
        frame.set_hexpand(True)
        return frame

    def _scrolled(self, child: Gtk.Widget) -> Gtk.ScrolledWindow:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(child)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        return scrolled

    def _load_profile_list(self) -> None:
        was_loading = self._loading_ui
        self._loading_ui = True
        self.profile_combo.remove_all()
        for index, profile in enumerate(self.profiles):
            self.profile_combo.append(str(index), profile.get("name", "Unnamed Profile"))
        self.profile_combo.set_active(self.current_profile_index)
        self._loading_ui = was_loading

    def _load_profile_into_ui(self, profile: dict[str, Any]) -> None:
        self._loading_ui = True
        self.profile_name_entry.set_text(profile.get("name", ""))
        activation = profile.get("activation", {})
        activation_mode = activation.get("mode", "always")
        if activation_mode == "toggle":
            activation_mode = "always"
        self.activation_combo.set_active_id(activation_mode)
        _set_combo_text(self.hotkey_combo, str(activation.get("hotkey", "F13")), KEY_CHOICES)
        self.model_entry.set_text(str(self._profile_model_path(profile)))
        self._sync_recognition_settings_widgets(profile)
        self._load_command_list()
        self._loading_ui = False
        self._restart_hotkey_listener()

    def _clear_listbox(self, listbox: Gtk.ListBox) -> None:
        child = listbox.get_first_child()
        while child is not None:
            listbox.remove(child)
            child = listbox.get_first_child()

    def _load_command_list(self) -> None:
        was_loading = self._loading_ui
        self._loading_ui = True
        self._clear_listbox(self.command_list)
        commands = self._profile().get("commands", [])
        self.current_command_index = min(self.current_command_index, max(0, len(commands) - 1))

        for index, command in enumerate(commands):
            row = Gtk.ListBoxRow()
            row.command_index = index  # type: ignore[attr-defined]

            row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)
            row_box.set_margin_start(8)
            row_box.set_margin_end(8)

            name_label = Gtk.Label(label=command.get("name", "Unnamed Command"))
            name_label.set_xalign(0)
            name_label.add_css_class("heading")
            row.name_label = name_label  # type: ignore[attr-defined]
            row_box.append(name_label)

            phrase_count = len(command.get("phrases", []))
            action_count = len(command.get("actions", []))
            summary = Gtk.Label(
                label=(
                    f"{phrase_count} phrase{'s' if phrase_count != 1 else ''} - "
                    f"{action_count} action{'s' if action_count != 1 else ''}"
                )
            )
            summary.set_xalign(0)
            summary.add_css_class("dim-label")
            row.summary_label = summary  # type: ignore[attr-defined]
            row_box.append(summary)

            row.set_child(row_box)
            self.command_list.append(row)

        if commands:
            selected_row = self.command_list.get_row_at_index(self.current_command_index)
            if selected_row is not None:
                self.command_list.select_row(selected_row)
            self._load_command_into_ui(commands[self.current_command_index])
        else:
            self.command_name_entry.set_text("")
            self._set_command_confidence_override(False, 0.0)
            self.command_weight_spin.set_value(0.0)
            self.parallel_actions_switch.set_active(False)
            self._clear_phrase_rows()
            self._clear_action_rows()
        self._loading_ui = was_loading

    def _load_command_into_ui(self, command: dict[str, Any]) -> None:
        was_loading = self._loading_ui
        self._loading_ui = True
        self.command_name_entry.set_text(command.get("name", ""))
        has_override = command.get("minConfidence") not in ("", None)
        self._set_command_confidence_override(
            has_override,
            _clamp_float(command.get("minConfidence"), 0.0, 1.0, 0.0),
        )
        self.command_weight_spin.set_value(_clamp_float(command.get("commandWeight"), 0.0, 1.0, 0.0))
        self.parallel_actions_switch.set_active(_command_action_mode(command) == "parallel")
        self._clear_phrase_rows()
        for phrase in command.get("phrases", []):
            self._add_phrase_row(str(phrase))
        self._clear_action_rows()
        for action in command.get("actions", []):
            self._add_action_row(action)
        self._loading_ui = was_loading

    def _profile(self) -> dict[str, Any]:
        return self.profiles[self.current_profile_index]

    def _settings_model_path(self) -> Path:
        return _model_path_from_value(self.settings.get("modelPath")) or default_model_path()

    def _profile_model_path(self, profile: dict[str, Any]) -> Path:
        speech = profile.get("speech", {})
        return _model_path_from_value(speech.get("modelPath")) or self._settings_model_path()

    def _speech_settings(self) -> dict[str, Any]:
        profile = self._profile() if self.profiles else {}
        speech = dict(DEFAULT_SPEECH_SETTINGS)
        if isinstance(profile, dict):
            profile_speech = profile.get("speech", {})
            if isinstance(profile_speech, dict):
                speech.update(profile_speech)
        speech["engine"] = "vosk"
        return speech

    def _sync_recognition_settings_widgets(self, profile: dict[str, Any]) -> None:
        if not hasattr(self, "min_confidence_spin"):
            return

        speech = dict(DEFAULT_SPEECH_SETTINGS)
        profile_speech = profile.get("speech", {})
        if isinstance(profile_speech, dict):
            speech.update(profile_speech)

        self.min_confidence_spin.set_value(_clamp_float(speech.get("minConfidence"), 0.0, 1.0, 0.0))
        self.min_unrecognized_confidence_spin.set_value(
            _clamp_float(speech.get("minUnrecognizedConfidence"), 0.0, 1.0, 0.0)
        )
        self.show_confidence_switch.set_active(bool(speech.get("showConfidence", True)))
        self.command_cooldown_spin.set_value(
            _nonnegative_int(str(speech.get("commandCooldownMs", 450)))
        )
        self.recognized_delay_spin.set_value(_nonnegative_int(str(speech.get("recognizedDelayMs", 0))))
        self.unrecognized_delay_spin.set_value(
            _nonnegative_int(str(speech.get("unrecognizedDelayMs", 100)))
        )
        self.max_alternatives_spin.set_value(
            max(0, min(10, _nonnegative_int(str(speech.get("maxAlternatives", 0)))))
        )
        self.response_speed_combo.set_active_id(
            str(speech.get("responseSpeed") or DEFAULT_SPEECH_SETTINGS["responseSpeed"])
        )
        if self.response_speed_combo.get_active_id() is None:
            self.response_speed_combo.set_active_id(DEFAULT_SPEECH_SETTINGS["responseSpeed"])
        self.trigger_guard_combo.set_active_id(
            str(speech.get("triggerGuard") or DEFAULT_SPEECH_SETTINGS["triggerGuard"])
        )
        if self.trigger_guard_combo.get_active_id() is None:
            self.trigger_guard_combo.set_active_id(DEFAULT_SPEECH_SETTINGS["triggerGuard"])

    def _set_command_confidence_override(self, enabled: bool, value: float) -> None:
        self.command_min_confidence_switch.set_active(enabled)
        self.command_min_confidence_spin.set_value(value)
        self.command_min_confidence_spin.set_sensitive(enabled)

    def _on_command_min_confidence_override_changed(self, switch: Gtk.Switch, _param) -> None:
        self.command_min_confidence_spin.set_sensitive(switch.get_active())
        if not self._loading_ui:
            self._queue_autosave()

    def _confirm_reset_settings_to_defaults(self, _button: Gtk.Button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Reset Settings To Defaults?",
            body=(
                "This resets app setup, activation, recognition, "
                "and per-command confidence tuning for the current profile. Command names, "
                "spoken phrases, and actions are kept."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset Defaults")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        if hasattr(dialog, "set_response_appearance"):
            dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_settings_response)
        dialog.present()

    def _on_reset_settings_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        if response == "reset":
            self._reset_settings_to_defaults()
        dialog.destroy()

    def _reset_settings_to_defaults(self) -> None:
        self._stop_listening()
        self._cancel_queued_autosave()

        defaults = default_settings()
        defaults["firstRunComplete"] = True
        self.settings = defaults

        profile = self._profile()
        profile["schemaVersion"] = 2
        profile["activation"] = {"mode": "always", "hotkey": "F13"}
        profile["speech"] = {
            **copy.deepcopy(DEFAULT_SPEECH_SETTINGS),
            "modelPath": str(default_model_path()),
        }
        for command in profile.get("commands", []):
            if not isinstance(command, dict):
                continue
            command.pop("confidence", None)
            command.pop("minConfidence", None)
            command["commandWeight"] = 0.0

        save_settings(self.settings)
        save_profile(profile)
        self._apply_start_on_login_setting(silent=True)
        self._sync_app_settings_widgets()
        self._load_microphones()
        self._load_profile_into_ui(profile)
        self._refresh_setup_status()
        self._start_mic_monitor()
        self._log("Settings reset to defaults; command phrases and actions were kept")

    def _on_profile_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._loading_ui:
            return
        active_id = combo.get_active_id()
        if active_id is None:
            return
        self._cancel_queued_autosave()
        self._save_current_profile(None, silent=True)
        self.current_profile_index = int(active_id)
        self.current_command_index = 0
        self._load_profile_list()
        self._load_profile_into_ui(self._profile())

    def _on_command_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._loading_ui:
            return
        if row is None:
            return
        next_index = int(getattr(row, "command_index", self.current_command_index))
        if next_index == self.current_command_index:
            return
        self._cancel_queued_autosave()
        try:
            self._save_command_edits()
        except Exception as exc:
            self._show_error("Could Not Save Command", str(exc))
            previous_row = self.command_list.get_row_at_index(self.current_command_index)
            if previous_row is not None:
                self._loading_ui = True
                self.command_list.select_row(previous_row)
                self._loading_ui = False
            return
        self.current_command_index = next_index
        commands = self._profile().get("commands", [])
        if self.current_command_index < len(commands):
            self._load_command_into_ui(commands[self.current_command_index])

    def _on_command_name_changed(self, entry: Gtk.Entry) -> None:
        if self._loading_ui:
            return
        selected_row = self.command_list.get_selected_row()
        if selected_row is not None and getattr(selected_row, "command_index", None) == self.current_command_index:
            name_label = getattr(selected_row, "name_label", None)
            if name_label is not None:
                name_label.set_text(entry.get_text().strip() or "Unnamed Command")
        self._queue_autosave()

    def _new_profile(self, _button: Gtk.Button) -> None:
        self._save_current_profile(None, silent=True)
        speech = copy.deepcopy(DEFAULT_SPEECH_SETTINGS)
        speech["modelPath"] = self.model_entry.get_text().strip() or str(default_model_path())
        profile = {
            "schemaVersion": 2,
            "id": str(uuid.uuid4()),
            "name": "New Game Profile",
            "target": {"kind": "manual"},
            "activation": {"mode": "always", "hotkey": "F13"},
            "speech": speech,
            "commands": [],
        }
        self.profiles.append(profile)
        self.current_profile_index = len(self.profiles) - 1
        self._load_profile_list()
        self._load_profile_into_ui(profile)
        save_profile(profile)
        self._log(f"Created profile: {profile['name']}")

    def _duplicate_profile(self, _button: Gtk.Button) -> None:
        self._save_current_profile(None, silent=True)
        source = self._profile()
        profile = copy.deepcopy(source)
        profile["id"] = str(uuid.uuid4())
        profile["name"] = f"{source.get('name', 'Profile')} Copy"
        for command in profile.get("commands", []):
            command["id"] = str(uuid.uuid4())
        try:
            validate_profile(profile)
            save_profile(profile)
        except Exception as exc:
            self._show_error("Could Not Duplicate Profile", str(exc))
            return
        self.profiles.append(profile)
        self.current_profile_index = len(self.profiles) - 1
        self.current_command_index = 0
        self._load_profile_list()
        self._load_profile_into_ui(profile)
        self._log(f"Duplicated profile: {profile['name']}")

    def _save_current_profile(self, _button: Gtk.Button | None = None, silent: bool = False) -> bool:
        try:
            self._save_command_edits()
            profile = self._profile()
            profile["name"] = self.profile_name_entry.get_text().strip() or "Unnamed Profile"
            profile["activation"] = {
                "mode": self.activation_combo.get_active_id() or "always",
                "hotkey": self.hotkey_combo.get_active_id() or "F13",
            }
            profile["schemaVersion"] = 2
            profile.setdefault("speech", {})
            speech_update = {
                "engine": "vosk",
                "language": "en-US",
                "modelPath": self.model_entry.get_text().strip() or str(default_model_path()),
                "showConfidence": self.show_confidence_switch.get_active(),
                "minConfidence": round(float(self.min_confidence_spin.get_value()), 2),
                "minUnrecognizedConfidence": round(
                    float(self.min_unrecognized_confidence_spin.get_value()),
                    2,
                ),
                "commandCooldownMs": int(self.command_cooldown_spin.get_value()),
                "recognizedDelayMs": int(self.recognized_delay_spin.get_value()),
                "unrecognizedDelayMs": int(self.unrecognized_delay_spin.get_value()),
                "maxAlternatives": int(self.max_alternatives_spin.get_value()),
                "responseSpeed": self.response_speed_combo.get_active_id()
                or DEFAULT_SPEECH_SETTINGS["responseSpeed"],
                "triggerGuard": self.trigger_guard_combo.get_active_id()
                or DEFAULT_SPEECH_SETTINGS["triggerGuard"],
            }
            profile["speech"].update(speech_update)
            profile["speech"].pop("sherpaKwsModelPath", None)
            validate_profile(profile)
            save_profile(profile)
            self._load_profile_list()
            self._last_autosave_error = None
            if not silent:
                self._log(f"Saved profile: {profile['name']}")
            return True
        except Exception as exc:
            if silent:
                message = str(exc)
                if message != self._last_autosave_error:
                    self._log(f"Autosave paused: {message}")
                    self._last_autosave_error = message
            else:
                self._show_error("Could Not Save Profile", str(exc))
            return False

    def _queue_autosave(self) -> None:
        if self._loading_ui:
            return
        self._cancel_queued_autosave()
        self._autosave_source_id = GLib.timeout_add(500, self._autosave_current_profile)

    def _cancel_queued_autosave(self) -> None:
        if self._autosave_source_id is not None:
            GLib.source_remove(self._autosave_source_id)
            self._autosave_source_id = None

    def _autosave_current_profile(self) -> bool:
        self._autosave_source_id = None
        self._save_current_profile(None, silent=True)
        return False

    def _delete_current_profile(self, _button: Gtk.Button) -> None:
        if len(self.profiles) <= 1:
            self._show_error("Cannot Delete Profile", "At least one profile is required.")
            return
        self._cancel_queued_autosave()
        profile = self._profile()
        delete_profile(profile)
        del self.profiles[self.current_profile_index]
        self.current_profile_index = 0
        self.current_command_index = 0
        self._load_profile_list()
        self._load_profile_into_ui(self._profile())

    def _add_command(self, _button: Gtk.Button) -> None:
        self._cancel_queued_autosave()
        self._save_command_edits()
        command = {
            "id": str(uuid.uuid4()),
            "name": "New Command",
            "commandWeight": 0.0,
            "actionExecution": "sequential",
            "phrases": ["new command"],
            "actions": [{"type": "key_press", "key": "R"}],
        }
        self._profile().setdefault("commands", []).append(command)
        self.current_command_index = len(self._profile()["commands"]) - 1
        self._load_command_list()
        self._queue_autosave()

    def _duplicate_command(self, _button: Gtk.Button) -> None:
        commands = self._profile().get("commands", [])
        if not commands:
            return
        self._cancel_queued_autosave()
        self._save_command_edits()
        source = commands[self.current_command_index]
        command = copy.deepcopy(source)
        command["id"] = str(uuid.uuid4())
        command["name"] = f"{source.get('name', 'Command')} Copy"
        insert_at = self.current_command_index + 1
        commands.insert(insert_at, command)
        self.current_command_index = insert_at
        self._load_command_list()
        self._queue_autosave()

    def _delete_command(self, _button: Gtk.Button) -> None:
        commands = self._profile().get("commands", [])
        if not commands:
            return
        del commands[self.current_command_index]
        self.current_command_index = max(0, self.current_command_index - 1)
        self._load_command_list()
        self._queue_autosave()

    def _save_command_edits(self) -> None:
        commands = self._profile().get("commands", [])
        if not commands or self.current_command_index >= len(commands):
            return
        command = commands[self.current_command_index]
        command["name"] = self.command_name_entry.get_text().strip() or "Unnamed Command"
        command.pop("confidence", None)
        if self.command_min_confidence_switch.get_active():
            command["minConfidence"] = round(float(self.command_min_confidence_spin.get_value()), 2)
        else:
            command.pop("minConfidence", None)
        command["commandWeight"] = round(float(self.command_weight_spin.get_value()), 2)
        command["phrases"] = self._phrase_values()
        command["actions"] = [row.to_action() for row in self.action_rows]
        command["actionExecution"] = (
            "parallel" if self.parallel_actions_switch.get_active() else "sequential"
        )
        self._refresh_selected_command_row()

    def _refresh_selected_command_row(self) -> None:
        commands = self._profile().get("commands", [])
        if not commands or self.current_command_index >= len(commands):
            return
        row = self.command_list.get_row_at_index(self.current_command_index)
        if row is None:
            return
        command = commands[self.current_command_index]
        name_label = getattr(row, "name_label", None)
        if name_label is not None:
            name_label.set_text(command.get("name", "Unnamed Command"))
        summary_label = getattr(row, "summary_label", None)
        if summary_label is not None:
            phrase_count = len(command.get("phrases", []))
            action_count = len(command.get("actions", []))
            summary_label.set_text(
                f"{phrase_count} phrase{'s' if phrase_count != 1 else ''} - "
                f"{action_count} action{'s' if action_count != 1 else ''}"
            )

    def _add_phrase_from_entry(self, _button: Gtk.Button) -> None:
        phrase = self.new_phrase_entry.get_text().strip()
        if not phrase:
            return
        self._add_phrase_row(phrase)
        self.new_phrase_entry.set_text("")
        self._queue_autosave()

    def _add_phrase_row(self, phrase: str) -> None:
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_text(phrase)
        entry.set_placeholder_text("Phrase, example: reload")
        entry.set_tooltip_text("One spoken phrase that triggers the selected command.")
        entry.connect("changed", lambda _entry: self._queue_autosave())

        remove_button = Gtk.Button(label="Remove")
        remove_button.set_tooltip_text("Remove this spoken phrase.")
        remove_button.connect("clicked", lambda _button: self._remove_phrase_row(row, entry))

        box.append(entry)
        box.append(remove_button)
        row.set_child(box)
        self.phrase_rows.append(entry)
        self.phrases_list.append(row)

    def _remove_phrase_row(self, row: Gtk.ListBoxRow, entry: Gtk.Entry) -> None:
        if entry in self.phrase_rows:
            self.phrase_rows.remove(entry)
        self.phrases_list.remove(row)
        self._queue_autosave()

    def _clear_phrase_rows(self) -> None:
        self.phrase_rows.clear()
        self._clear_listbox(self.phrases_list)

    def _phrase_values(self) -> list[str]:
        return [
            entry.get_text().strip()
            for entry in self.phrase_rows
            if entry.get_text().strip()
        ]

    def _add_action(self, _button: Gtk.Button) -> None:
        self._add_action_row()
        self._queue_autosave()

    def _add_action_row(self, action: dict[str, Any] | None = None, index: int | None = None) -> None:
        row = ActionRow(
            action,
            self._remove_action_row,
            lambda _row: self._queue_autosave(),
            self._capture_next_key,
            self._move_action_row,
            self._copy_action_row,
            self._paste_action_after_row,
        )
        if index is None or index >= len(self.action_rows):
            self.action_rows.append(row)
        else:
            self.action_rows.insert(max(0, index), row)
        self._install_action_drag(row)
        self._refresh_action_row_widgets()

    def _remove_action_row(self, row: ActionRow) -> None:
        if row in self.action_rows:
            self.action_rows.remove(row)
        self.actions_box.remove(row.box)
        self._refresh_action_row_widgets()
        self._queue_autosave()

    def _clear_action_rows(self) -> None:
        for row in list(self.action_rows):
            self.actions_box.remove(row.box)
        self.action_rows.clear()

    def _move_action_row(self, row: ActionRow, direction: int) -> None:
        if row not in self.action_rows:
            return
        old_index = self.action_rows.index(row)
        new_index = max(0, min(len(self.action_rows) - 1, old_index + direction))
        if new_index == old_index:
            return
        self.action_rows.pop(old_index)
        self.action_rows.insert(new_index, row)
        self._refresh_action_row_widgets()
        self._queue_autosave()

    def _install_action_drag(self, row: ActionRow) -> None:
        try:
            drag_source = Gtk.DragSource()
            drag_source.set_actions(Gdk.DragAction.MOVE)
            drag_source.connect("prepare", self._prepare_action_drag)
            drag_source.connect("drag-begin", lambda _source, _drag: self._set_dragged_action(row))
            drag_source.connect("drag-end", lambda _source, _drag, _delete_data: self._clear_dragged_action())
            row.box.add_controller(drag_source)

            drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            drop_target.connect("drop", lambda _target, _value, _x, y: self._drop_action_row(row, y))
            row.box.add_controller(drop_target)
        except Exception:
            pass

    def _prepare_action_drag(self, _source, _x: float, _y: float):
        try:
            return Gdk.ContentProvider.new_for_value("nome-voice-action-row")
        except Exception:
            return None

    def _set_dragged_action(self, row: ActionRow) -> None:
        self._dragged_action_row = row

    def _clear_dragged_action(self) -> None:
        self._dragged_action_row = None

    def _drop_action_row(self, target_row: ActionRow, y: float) -> bool:
        source_row = self._dragged_action_row
        if source_row is None or source_row is target_row:
            return False
        if source_row not in self.action_rows or target_row not in self.action_rows:
            return False

        self.action_rows.remove(source_row)
        target_index = self.action_rows.index(target_row)
        if y > target_row.box.get_height() / 2:
            target_index += 1
        self.action_rows.insert(target_index, source_row)
        self._refresh_action_row_widgets()
        self._queue_autosave()
        return True

    def _copy_action_row(self, row: ActionRow) -> None:
        self._action_clipboard = copy.deepcopy(row.to_action())
        self._log("Copied action")

    def _paste_action_after_row(self, row: ActionRow) -> None:
        if row not in self.action_rows:
            return
        self._paste_action_at(self.action_rows.index(row) + 1)

    def _paste_action_at_end(self) -> None:
        self._paste_action_at(len(self.action_rows))

    def _paste_action_at(self, index: int) -> None:
        if self._action_clipboard is None:
            self._log("No copied action to paste")
            return
        self._add_action_row(copy.deepcopy(self._action_clipboard), index=index)
        self._queue_autosave()
        self._log("Pasted action")

    def _refresh_action_row_widgets(self) -> None:
        child = self.actions_box.get_first_child()
        while child is not None:
            self.actions_box.remove(child)
            child = self.actions_box.get_first_child()
        total = len(self.action_rows)
        for index, row in enumerate(self.action_rows):
            row.set_order_state(index, total)
            self.actions_box.append(row.box)

    def _on_activation_changed(self, _combo: Gtk.ComboBoxText) -> None:
        if self._loading_ui:
            return
        self._refresh_status_button()
        self._queue_autosave()
        self._restart_hotkey_listener()

    def _on_hotkey_changed(self, _combo: Gtk.ComboBoxText) -> None:
        if self._loading_ui:
            return
        self._queue_autosave()
        self._restart_hotkey_listener()

    def _on_hotkey_combo_key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        key_name = _key_name_from_keyval(keyval)
        if key_name is None:
            return False
        self._set_hotkey(key_name)
        return True

    def _capture_next_key(self, callback: Callable[[str], None], button: Gtk.Button | None = None) -> None:
        if button is not None and button is self._key_capture_button:
            self._key_capture_callback = None
            self._clear_key_capture_feedback()
            self._log("Key capture cancelled")
            return
        self._clear_key_capture_feedback()
        self._key_capture_callback = callback
        self._key_capture_button = button
        if button is not None:
            self._key_capture_button_label = button.get_label() or "Press Key"
            button.set_label("Press a key...")
            button.add_css_class("suggested-action")
        self._log("Key capture ready. Press a key now.")

    def _clear_key_capture_feedback(self) -> None:
        if self._key_capture_button is not None:
            self._key_capture_button.remove_css_class("suggested-action")
            self._key_capture_button.set_label(self._key_capture_button_label or "Press Key")
        self._key_capture_button = None
        self._key_capture_button_label = ""

    def _set_hotkey(self, key_name: str) -> None:
        _set_combo_text(self.hotkey_combo, key_name, KEY_CHOICES)
        self._queue_autosave()
        self._restart_hotkey_listener()
        self._log(f"Global hotkey set to: {key_name}")

    def _on_window_key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        key_name = _key_name_from_keyval(keyval)
        if key_name is None:
            return False
        if self._key_capture_callback is not None:
            callback = self._key_capture_callback
            self._key_capture_callback = None
            callback(key_name)
            self._clear_key_capture_feedback()
            self._log(f"Captured key: {key_name}")
            return True
        if key_name == self._active_hotkey() and not self._focused_hotkey_down:
            self._focused_hotkey_down = True
            self._handle_hotkey_pressed()
            return True
        return False

    def _on_window_key_released(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        key_name = _key_name_from_keyval(keyval)
        if key_name is None:
            return False
        if key_name == self._active_hotkey():
            self._focused_hotkey_down = False
            self._handle_hotkey_released()
            return True
        return False

    def _active_hotkey(self) -> str:
        return self.hotkey_combo.get_active_id() or "F13"

    def _handle_hotkey_pressed(self) -> bool:
        mode = self.activation_combo.get_active_id() or "always"
        if mode == "push_to_talk":
            if not self.listening:
                self._start_listening()
        elif self.listening:
            self._stop_listening()
        else:
            self._start_listening()
        return False

    def _handle_hotkey_released(self) -> bool:
        if (self.activation_combo.get_active_id() or "always") == "push_to_talk" and self.listening:
            self._stop_listening()
        return False

    def _restart_hotkey_listener(self) -> None:
        if not hasattr(self, "hotkey_combo"):
            return
        self._stop_hotkey_listener()
        hotkey = self._active_hotkey()
        if not hotkey:
            return
        self._hotkey_status = "Starting global listener"
        self._global_hotkey_listener = GlobalHotkeyListener(
            hotkey=hotkey,
            on_press=lambda: GLib.idle_add(self._handle_hotkey_pressed),
            on_release=lambda: GLib.idle_add(self._handle_hotkey_released),
            on_status=lambda message: GLib.idle_add(self._set_hotkey_status, message),
        )
        self._global_hotkey_listener.start()

    def _stop_hotkey_listener(self) -> None:
        if self._global_hotkey_listener is not None:
            self._global_hotkey_listener.stop()
            self._global_hotkey_listener = None

    def _set_hotkey_status(self, message: str) -> bool:
        self._hotkey_status = message
        self._log(message)
        if hasattr(self, "setup_status_label"):
            self._refresh_setup_status()
        return False

    def _on_status_clicked(self, _button: Gtk.Button) -> None:
        mode = self.activation_combo.get_active_id() or "always"
        if mode == "push_to_talk":
            return
        if self.listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _on_status_pressed(self, _gesture, _n_press, _x, _y) -> None:
        if (self.activation_combo.get_active_id() or "always") == "push_to_talk":
            self._start_listening()

    def _on_status_released(self, _gesture, _n_press, _x, _y) -> None:
        if (self.activation_combo.get_active_id() or "always") == "push_to_talk":
            self._stop_listening()

    def _emergency_stop(self, _button: Gtk.Button | None = None) -> bool:
        self._cancel_queued_autosave()
        self._key_capture_callback = None
        self._clear_key_capture_feedback()
        self._focused_hotkey_down = False
        self._command_cooldowns.clear()
        self._recognition_delay_until = 0.0
        self._ignore_recognition_until = time.monotonic() + RESUME_IGNORE_SECONDS

        if self.recognizer is not None:
            self.recognizer.stop()
            self.recognizer = None
            self._recognizer_signature = None

        self.listening = False
        self.action_runner.stop_all()
        self._set_mic_meter_mode("paused")
        self._start_mic_monitor()
        self._refresh_status_button()
        self.last_command_label.set_text("Last command fired: emergency stop")
        self._log("Emergency stop: cancelled recognition and released held inputs")
        return False

    def _start_listening_on_launch(self) -> bool:
        if self.settings.get("startListeningOnLaunch") and not self.listening:
            self._start_listening()
        return False

    def _start_mic_monitor(self) -> None:
        if self._closing or self.listening or not hasattr(self, "microphone_combo"):
            return

        signature = (self._selected_microphone_id(),)
        if self.mic_monitor is not None and self._mic_monitor_signature == signature:
            return

        self._stop_mic_monitor(reset=False)
        self._set_mic_meter_mode("paused")
        self.mic_monitor = MicLevelMonitor(
            microphone_id=self._selected_microphone_id(),
            on_volume=lambda level: GLib.idle_add(self._update_mic_level, level),
            on_error=lambda message: GLib.idle_add(self._handle_mic_monitor_error, message),
        )
        self._mic_monitor_signature = signature
        self.mic_monitor.start()

    def _stop_mic_monitor(self, reset: bool = False) -> None:
        if self.mic_monitor is not None:
            self.mic_monitor.stop()
            self.mic_monitor = None
            self._mic_monitor_signature = None
        if reset and hasattr(self, "mic_level_bar"):
            self._update_mic_level(0.0)

    def _handle_mic_monitor_error(self, message: str) -> bool:
        if not self._mic_monitor_failure_logged:
            self._log(message)
            self._mic_monitor_failure_logged = True
        self.mic_monitor = None
        self._mic_monitor_signature = None
        return False

    def _set_mic_meter_mode(self, mode: str) -> None:
        if not hasattr(self, "mic_level_bar"):
            return
        self.mic_level_bar.remove_css_class("mic-level-active")
        self.mic_level_bar.remove_css_class("mic-level-paused")
        if mode == "active":
            self.mic_level_bar.add_css_class("mic-level-active")
            self.mic_level_bar.set_tooltip_text("Microphone level during active recognition.")
        else:
            self.mic_level_bar.add_css_class("mic-level-paused")
            self.mic_level_bar.set_tooltip_text(
                "Microphone level while recognition is paused."
            )

    def _start_listening(self) -> None:
        try:
            if not self._save_current_profile(None):
                return
            self._stop_mic_monitor(reset=False)
            self._set_mic_meter_mode("active")
            profile = copy.deepcopy(self._profile())
            model_path = self._profile_model_path(profile)
            phrases = command_phrases(profile)
            response_speed = self._speech_choice_setting("responseSpeed")
            trigger_guard = self._speech_choice_setting("triggerGuard")
            signature = (
                str(model_path),
                self._selected_microphone_id(),
                tuple(phrases),
                self._max_alternatives(),
                response_speed,
                trigger_guard,
            )

            if self.recognizer is not None and self._recognizer_signature == signature:
                self.listening = True
                self._ignore_recognition_until = time.monotonic() + RESUME_IGNORE_SECONDS
                self._refresh_status_button()
                self._log("Listening resumed")
                return

            if self.recognizer is not None:
                self.recognizer.stop()
                self.recognizer = None

            self.recognizer = VoiceRecognizer(
                model_path=model_path,
                microphone_id=self._selected_microphone_id(),
                phrases=phrases,
                on_heard=lambda result: GLib.idle_add(self._handle_heard, result),
                on_error=lambda message: GLib.idle_add(self._handle_speech_error, message),
                on_status=lambda message: GLib.idle_add(self._log, f"Speech: {message}"),
                on_volume=lambda level: GLib.idle_add(self._update_mic_level, level),
                on_debug=lambda message: GLib.idle_add(self._log, message),
                max_alternatives=self._max_alternatives(),
                response_speed=response_speed,
                guard_level=trigger_guard,
            )
            self._recognizer_signature = signature
            self.recognizer.start()
            self.listening = True
            self._ignore_recognition_until = time.monotonic() + RESUME_IGNORE_SECONDS
            self._refresh_status_button()
            self._log("Listening started")
        except Exception as exc:
            self.listening = False
            self._set_mic_meter_mode("paused")
            self._start_mic_monitor()
            self._show_error("Could Not Start Listening", str(exc))

    def _stop_listening(self, keep_warm: bool = False, restart_monitor: bool = True) -> None:
        if self.recognizer is not None and not keep_warm:
            self.recognizer.stop()
            self.recognizer = None
            self._recognizer_signature = None
        self.action_runner.release_all()
        self.listening = False
        if restart_monitor and not keep_warm:
            self._set_mic_meter_mode("paused")
            self._start_mic_monitor()
        elif not keep_warm:
            self._update_mic_level(0.0)
        self._refresh_status_button()
        self._log("Listening paused" if keep_warm else "Listening stopped")

    def _handle_heard(self, result: dict[str, Any]) -> bool:
        text = self._result_text(result)
        confidence = self._result_confidence(result)
        self.last_heard_label.set_text(self._last_heard_text(text, confidence))

        if not self.listening:
            self._log(f"Ignored recognition while not listening (final): {text}")
            return False

        now = time.monotonic()
        if now < self._ignore_recognition_until:
            self._log(f"Ignored recognition during resume guard (final): {text}")
            return False

        match = self._best_final_match(result)
        if match is None:
            self._log_unmatched_final(text, confidence)
            self._apply_unrecognized_delay()
            return False

        if now < self._recognition_delay_until:
            match["reason"] = "post-result delay"
            self._log_final_match(match, accepted=False)
            return False

        if not bool(match.get("accepted")):
            self._log_final_match(match, accepted=False)
            self._apply_unrecognized_delay()
            return False

        # Press the keys first; the log can wait a few milliseconds.
        fired = self._execute_command(match["command"])
        if fired:
            self._apply_recognized_delay()
        else:
            self._apply_unrecognized_delay()
        self._log_final_match(match, accepted=True)
        if fired:
            self._log(
                f"Command fired ({self._result_timing_text(result)}): {match['command'].get('name')}"
            )
        return False

    def _execute_command(self, command: dict[str, Any]) -> bool:
        now = time.monotonic()
        if now < self._recognition_delay_until:
            remaining_ms = max(0, round((self._recognition_delay_until - now) * 1000))
            self._log(
                f"Command suppressed by recognition delay: {command.get('name')} "
                f"({remaining_ms} ms remaining)"
            )
            return False
        command_id = str(command.get("id", command.get("name", "")))
        cooldown_until = self._command_cooldowns.get(command_id, 0.0)
        if now < cooldown_until:
            remaining_ms = max(0, round((cooldown_until - now) * 1000))
            self._log(
                f"Command suppressed by cooldown: {command.get('name')} "
                f"({remaining_ms} ms remaining)"
            )
            return False
        self._command_cooldowns[command_id] = now + self._command_cooldown_seconds()

        self.action_runner.execute_async(
            command.get("actions", []),
            on_error=lambda message: GLib.idle_add(self._show_error, "Input Error", message),
            mode=_command_action_mode(command),
        )
        self.last_command_label.set_text(f"Last command fired: {command.get('name')}")
        return True

    def _handle_special_action(self, action: dict[str, Any]) -> bool:
        action_type = action.get("type")
        if action_type == "nome_emergency_stop":
            return self._emergency_stop(None)
        if action_type == "nome_stop_listening":
            self._stop_listening()
            self.last_command_label.set_text("Last command fired: pause listening")
            self._log("Nome Voice action: paused listening")
            return False
        self._log(f"Ignored unknown Nome Voice action: {action_type}")
        return False

    def _result_timing_text(self, result: dict[str, Any]) -> str:
        path = "quick path" if result.get("path") == "quick" else "main path"
        parts = [path]
        ended_ms = result.get("endedMs")
        if isinstance(ended_ms, (int, float)):
            parts.append(f"{int(ended_ms)} ms after you stopped")
        decided_at = result.get("decidedAt")
        if isinstance(decided_at, (int, float)):
            parts.append(f"+{max(0, round((time.monotonic() - decided_at) * 1000))} ms to send")
        return ", ".join(parts)

    def _best_final_match(self, result: dict[str, Any]) -> dict[str, Any] | None:
        return scored_best_final_match(self._profile(), result, self._speech_settings())

    def _result_text(self, result: dict[str, Any]) -> str:
        return scored_result_text(result)

    def _result_confidence(self, result: dict[str, Any]) -> float:
        return scored_result_confidence(result)

    def _last_heard_text(self, text: str, confidence: float) -> str:
        if bool(self._speech_settings().get("showConfidence", True)):
            return f"Last heard: {text} (confidence {confidence:.2f})"
        return f"Last heard: {text}"

    def _log_final_match(self, match: dict[str, Any], accepted: bool) -> None:
        status = "accepted" if accepted else "rejected"
        score = match.get("alternativeScore")
        score_text = f"score={float(score):.2f}, " if score is not None else ""
        self._log(
            "Recognition "
            f"{status}: text='{match.get('text', '')}', "
            f"command='{match.get('commandName', '')}', "
            f"matched='{match.get('matchedPhrase', '')}', "
            f"{score_text}"
            f"raw={float(match.get('rawConfidence', 0.0)):.2f}, "
            f"effective={float(match.get('effectiveConfidence', 0.0)):.2f}, "
            f"threshold={float(match.get('threshold', 0.0)):.2f}, "
            f"weight={float(match.get('commandWeight', 0.0)):.2f}, "
            f"alternative={int(match.get('alternativeIndex', 0))}, "
            f"reason={match.get('reason', '')}"
        )

    def _log_unmatched_final(self, text: str, confidence: float) -> None:
        threshold = self._speech_float_setting("minUnrecognizedConfidence", 0.0)
        if confidence >= threshold:
            self._log(
                "Recognition rejected: "
                f"text='{text}', raw={confidence:.2f}, effective={confidence:.2f}, "
                f"threshold={threshold:.2f}, alternative=0, reason=no command matched"
            )
            return
        self._log(
            "Recognition ignored: "
            f"text='{text}', raw={confidence:.2f}, "
            f"threshold={threshold:.2f}, reason=below unrecognized log threshold"
        )

    def _apply_recognized_delay(self) -> None:
        delay = self._speech_int_setting("recognizedDelayMs", 0, minimum=0, maximum=5000) / 1000
        self._recognition_delay_until = max(self._recognition_delay_until, time.monotonic() + delay)

    def _apply_unrecognized_delay(self) -> None:
        delay = self._speech_int_setting("unrecognizedDelayMs", 100, minimum=0, maximum=5000) / 1000
        self._recognition_delay_until = max(self._recognition_delay_until, time.monotonic() + delay)

    def _speech_float_setting(self, key: str, fallback: float) -> float:
        return _clamp_float(self._speech_settings().get(key), 0.0, 1.0, fallback)

    def _speech_int_setting(
        self,
        key: str,
        fallback: int,
        minimum: int = 0,
        maximum: int = 10000,
    ) -> int:
        try:
            value = int(self._speech_settings().get(key, fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(maximum, value))

    def _max_alternatives(self) -> int:
        return self._speech_int_setting("maxAlternatives", 0, minimum=0, maximum=10)

    def _command_cooldown_seconds(self) -> float:
        return self._speech_int_setting("commandCooldownMs", 450, minimum=0, maximum=5000) / 1000

    def _run_selected_command(self, _button: Gtk.Button) -> None:
        try:
            self._save_command_edits()
            command = self._profile().get("commands", [])[self.current_command_index]
            self.last_command_label.set_text(f"Last command fired: {command.get('name')}")
            self.action_runner.execute_async(
                command.get("actions", []),
                on_error=lambda message: GLib.idle_add(self._show_error, "Input Error", message),
                mode=_command_action_mode(command),
            )
            self._log(f"Manual command run: {command.get('name')}")
        except Exception as exc:
            self._show_error("Could Not Run Command", str(exc))

    def _test_recognition(self, _button: Gtk.Button) -> None:
        phrase = self.test_phrase_entry.get_text().strip()
        if not phrase:
            self._show_error("Missing Phrase", "Enter a test phrase first.")
            return
        self._show_page("log")
        self._stop_listening(restart_monitor=False)
        try:
            self._stop_mic_monitor(reset=False)
            self._set_mic_meter_mode("active")
            model_path = Path(self.model_entry.get_text().strip() or self._settings_model_path()).expanduser()
            self.recognizer = VoiceRecognizer(
                model_path=model_path,
                microphone_id=self._selected_microphone_id(),
                phrases=[normalize_phrase(phrase)],
                response_speed=self._speech_choice_setting("responseSpeed"),
                guard_level=self._speech_choice_setting("triggerGuard"),
                on_heard=lambda result: GLib.idle_add(self._handle_test_heard, result),
                on_error=lambda message: GLib.idle_add(self._handle_speech_error, message),
                on_status=lambda message: GLib.idle_add(self._log, f"Speech test: {message}"),
                on_volume=lambda level: GLib.idle_add(self._update_mic_level, level),
                on_debug=lambda message: GLib.idle_add(self._log, message),
                max_alternatives=self._max_alternatives(),
            )
            self.recognizer.start()
            self.listening = True
            self._refresh_status_button()
            self._log(f"Recognition test started with Vosk. Say: {phrase}")
        except Exception as exc:
            self._show_error("Could Not Start Test", str(exc))

    def _handle_test_heard(self, result: dict[str, Any]) -> bool:
        text = self._result_text(result)
        confidence = self._result_confidence(result)
        self.last_heard_label.set_text(f"Last heard: {text}")
        self._log(f"Recognition test heard: {text} (confidence {confidence:.2f})")
        self._stop_listening()
        return False

    def _handle_speech_error(self, message: str) -> bool:
        self.recognizer = None
        self._recognizer_signature = None
        self.listening = False
        self.action_runner.release_all()
        self._set_mic_meter_mode("paused")
        self._start_mic_monitor()
        self._refresh_status_button()
        self._show_error("Speech Error", message)
        return False

    def _update_mic_level(self, level: float) -> bool:
        fraction = max(0.0, min(1.0, float(level)))
        self._last_mic_level = fraction
        self.mic_level_bar.set_fraction(fraction)
        self.mic_level_label.set_text(f"{round(fraction * 100):d}%")
        return False

    def _on_speech_float_changed(self, spin_button: Gtk.SpinButton, setting_key: str) -> None:
        if self._loading_ui:
            return
        self._profile().setdefault("speech", {})[setting_key] = round(float(spin_button.get_value()), 2)
        self._queue_autosave()

    def _on_speech_integer_changed(self, spin_button: Gtk.SpinButton, setting_key: str) -> None:
        if self._loading_ui:
            return
        self._profile().setdefault("speech", {})[setting_key] = int(spin_button.get_value())
        self._queue_autosave()

    def _on_speech_choice_changed(self, combo: Gtk.ComboBoxText, setting_key: str) -> None:
        if self._loading_ui:
            return
        choice = combo.get_active_id()
        if not choice:
            return
        self._profile().setdefault("speech", {})[setting_key] = choice
        self._queue_autosave()
        if self.listening and self.recognizer is not None:
            # the recognizer is rebuilt because its settings signature changed
            self._start_listening()

    def _speech_choice_setting(self, key: str) -> str:
        value = self._speech_settings().get(key)
        return str(value) if value else str(DEFAULT_SPEECH_SETTINGS[key])

    def _on_speech_boolean_changed(self, switch: Gtk.Switch, setting_key: str) -> None:
        if self._loading_ui:
            return
        self._profile().setdefault("speech", {})[setting_key] = switch.get_active()
        self._queue_autosave()

    def _on_start_listening_on_launch_changed(self, switch: Gtk.Switch, _param) -> None:
        if self._loading_ui:
            return
        enabled = switch.get_active()
        self.settings["startListeningOnLaunch"] = enabled
        save_settings(self.settings)
        self._log(
            "Start listening on launch enabled"
            if enabled
            else "Start listening on launch disabled"
        )

    def _on_start_on_login_changed(self, switch: Gtk.Switch, _param) -> None:
        if self._loading_ui:
            return
        enabled = switch.get_active()
        self.settings["startOnLogin"] = enabled
        save_settings(self.settings)
        if self._apply_start_on_login_setting():
            self._log("System startup enabled" if enabled else "System startup disabled")
            return

        self._loading_ui = True
        try:
            switch.set_active(not enabled)
        finally:
            self._loading_ui = False
        self.settings["startOnLogin"] = not enabled
        save_settings(self.settings)

    def _sync_app_settings_widgets(self) -> None:
        self._loading_ui = True
        try:
            if hasattr(self, "start_listening_on_launch_switch"):
                self.start_listening_on_launch_switch.set_active(
                    bool(self.settings.get("startListeningOnLaunch", False))
                )
            if hasattr(self, "start_on_login_switch"):
                self.start_on_login_switch.set_active(
                    bool(self.settings.get("startOnLogin", False))
                )
        finally:
            self._loading_ui = False

    def _apply_start_on_login_setting(self, silent: bool = False) -> bool:
        try:
            _set_autostart_enabled(bool(self.settings.get("startOnLogin", False)))
        except OSError as exc:
            if not silent:
                self._show_error("Could Not Update Startup Setting", str(exc))
            else:
                self._log(f"Could not update startup setting: {exc}")
            return False
        return True

    def _save_setup(self, _button: Gtk.Button) -> None:
        microphone_id = self._selected_microphone_id()
        model_path = self.model_entry.get_text().strip() or str(default_model_path())
        self.settings.pop("speechEngine", None)
        self.settings.pop("sherpaKwsModelPath", None)
        self.settings["modelPath"] = model_path
        self.settings["microphoneId"] = microphone_id
        self.settings["microphoneIndex"] = self._microphone_index_from_id(microphone_id)
        self.settings["firstRunComplete"] = True
        save_settings(self.settings)
        self._queue_autosave()
        self._refresh_setup_status()
        self._start_mic_monitor()
        self._log("Setup saved")

    def _refresh_microphones(self, _button: Gtk.Button | None = None) -> None:
        self._load_microphones()
        self._refresh_setup_status()
        self._start_mic_monitor()
        self._log("Microphone list refreshed")

    def _on_microphone_changed(self, _combo: Gtk.ComboBoxText) -> None:
        if self._loading_microphones:
            return
        self._start_mic_monitor()

    def _load_microphones(self) -> None:
        self._loading_microphones = True
        try:
            self.microphone_combo.remove_all()
            microphones = list_microphones()
            selected_id = self._saved_microphone_id()
            self.microphone_combo.append("default", "System default microphone (recommended)")
            for microphone_id, name in microphones:
                self.microphone_combo.append(microphone_id, name)
            selected_exists = selected_id == "default" or any(
                microphone_id == selected_id for microphone_id, _name in microphones
            )
            if selected_exists:
                self.microphone_combo.set_active_id(selected_id)
            else:
                self.microphone_combo.set_active_id("default")
        finally:
            self._loading_microphones = False

    def _selected_microphone_id(self) -> str:
        active_id = self.microphone_combo.get_active_id()
        return active_id or "default"

    def _saved_microphone_id(self) -> str:
        microphone_id = self.settings.get("microphoneId")
        if isinstance(microphone_id, str) and microphone_id:
            return microphone_id

        legacy_index = self.settings.get("microphoneIndex")
        if legacy_index is not None:
            return f"sd:{legacy_index}"
        return "default"

    def _microphone_index_from_id(self, microphone_id: str) -> int | None:
        if not microphone_id.startswith("sd:"):
            return None
        try:
            return int(microphone_id.split(":", 1)[1])
        except ValueError:
            return None

    def _refresh_setup_status(self) -> None:
        model_path = Path(
            self.model_entry.get_text().strip() or self._settings_model_path()
        ).expanduser()
        input_ok, input_message = self.action_runner.available()
        microphones = list_microphones()
        lines = [
            "Engine: Vosk speech recognition",
            f"Model: {'OK' if model_path.exists() else 'Missing'} - {model_path}",
            f"Microphones: {len(microphones)} found",
            f"Input: {'OK' if input_ok else 'Needs setup'} - {input_message}",
            f"Hotkey: {self._active_hotkey()} - {self._hotkey_status}",
        ]
        self.setup_status_label.set_text("\n".join(lines))

    def _refresh_status_button(self) -> None:
        mode = self.activation_combo.get_active_id() if hasattr(self, "activation_combo") else "always"
        if self.listening:
            text = "Listening"
            self._set_mic_meter_mode("active")
        elif mode == "push_to_talk":
            text = "Hold To Talk"
            self._set_mic_meter_mode("paused")
        else:
            text = "Paused"
            self._set_mic_meter_mode("paused")
        self.status_button.set_label(text)

    def _import_profile_dialog(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileChooserNative(
            title="Import Profile",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Import",
            cancel_label="Cancel",
        )
        dialog.connect("response", self._on_import_response)
        dialog.show()

    def _on_import_response(self, dialog: Gtk.FileChooserNative, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file and file.get_path():
                try:
                    profile = import_profile(Path(file.get_path()))
                    self.profiles.append(profile)
                    self.current_profile_index = len(self.profiles) - 1
                    self._load_profile_list()
                    self._load_profile_into_ui(profile)
                    self._log(f"Imported profile: {profile.get('name')}")
                except Exception as exc:
                    self._show_error("Import Failed", str(exc))
        dialog.destroy()

    def _export_profile_dialog(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileChooserNative(
            title="Export Profile",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Export",
            cancel_label="Cancel",
        )
        dialog.set_current_name(f"{self._profile().get('name', 'profile')}.json")
        dialog.connect("response", self._on_export_response)
        dialog.show()

    def _on_export_response(self, dialog: Gtk.FileChooserNative, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file and file.get_path():
                try:
                    if not self._save_current_profile(None):
                        return
                    export_profile(self._profile(), Path(file.get_path()))
                    self._log(f"Exported profile to {file.get_path()}")
                except Exception as exc:
                    self._show_error("Export Failed", str(exc))
        dialog.destroy()

    def _show_first_run_dialog(self) -> bool:
        self.settings["firstRunComplete"] = True
        save_settings(self.settings)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="First Run Setup",
            body=(
                "Check the microphone, Vosk model, and virtual input status before playing. "
                "Settings will open now."
            ),
        )
        dialog.add_response("ok", "Open Settings")
        dialog.set_default_response("ok")
        dialog.connect("response", lambda _dialog, _response: self._show_settings())
        dialog.present()
        return False

    def _show_error(self, heading: str, message: str) -> bool:
        self._log(f"{heading}: {message}")
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=message)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.present()
        return False

    def _log(self, message: str) -> bool:
        buffer = self.log_view.get_buffer()
        end = buffer.get_end_iter()
        buffer.insert(end, f"{message}\n")
        extra_lines = buffer.get_line_count() - LOG_MAX_LINES
        if extra_lines > 0:
            buffer.delete(buffer.get_start_iter(), buffer.get_iter_at_line(extra_lines)[1])
        return False

    def _get_text_view(self, text_view: Gtk.TextView) -> str:
        buffer = text_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        return buffer.get_text(start, end, True)

    def _set_text_view(self, text_view: Gtk.TextView, text: str) -> None:
        text_view.get_buffer().set_text(text)

    def _show_page(self, name: str) -> None:
        if hasattr(self, "stack"):
            self._show_main()
            self.stack.set_visible_child_name(name)
            self._refresh_nav_buttons()

    def _show_settings(self) -> None:
        if hasattr(self, "root_stack"):
            self.root_stack.set_visible_child_name("settings")

    def _show_main(self) -> None:
        if hasattr(self, "root_stack"):
            self.root_stack.set_visible_child_name("main")

    def _refresh_nav_buttons(self) -> None:
        if not hasattr(self, "stack"):
            return
        visible = self.stack.get_visible_child_name()
        for button, page_name in [
            (getattr(self, "commands_nav_button", None), "commands"),
            (getattr(self, "log_nav_button", None), "log"),
        ]:
            if button is None:
                continue
            if visible == page_name:
                button.add_css_class("suggested-action")
            else:
                button.remove_css_class("suggested-action")

    def do_close_request(self) -> bool:
        self._closing = True
        self._stop_hotkey_listener()
        self._cancel_queued_autosave()
        self._save_current_profile(None, silent=True)
        self._stop_mic_monitor(reset=True)
        self._stop_listening(restart_monitor=False)
        self.action_runner.close()
        return False


def _autostart_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "autostart" / AUTOSTART_DESKTOP_FILE


def _legacy_autostart_paths() -> list[Path]:
    autostart_dir = _autostart_path().parent
    return [autostart_dir / name for name in LEGACY_DESKTOP_FILES]


def _autostart_exec_command() -> str:
    launcher = Path.home() / ".local" / "bin" / "nome-voice-launcher"
    if launcher.exists():
        return _desktop_exec_escape(str(launcher))
    return "nome-voice"


def _desktop_exec_escape(value: str) -> str:
    if any(character.isspace() for character in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _set_autostart_enabled(enabled: bool) -> None:
    path = _autostart_path()
    if not enabled:
        for candidate in [path, *_legacy_autostart_paths()]:
            if candidate.exists():
                candidate.unlink()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in _legacy_autostart_paths():
        if candidate.exists() and candidate != path:
            candidate.unlink()
    path.write_text(
        AUTOSTART_DESKTOP_ENTRY.format(
            app_id=APP_ID,
            exec_command=_autostart_exec_command(),
            icon_name=APP_ICON_NAME,
        ),
        encoding="utf-8",
    )


def _configure_app_identity() -> None:
    GLib.set_application_name(APP_NAME)
    GLib.set_prgname(APP_NAME)
    if os.name != "posix":
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        libc.prctl(15, APP_NAME.encode("utf-8"), 0, 0, 0)
    except Exception:
        pass


def main() -> int:
    _configure_app_identity()
    app = NomeVoiceApplication()
    return app.run(None)
