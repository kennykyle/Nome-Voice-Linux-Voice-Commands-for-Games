"""Profile and settings persistence for Nome Voice."""

from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .matcher import build_phrase_map
from .paths import (
    default_model_path,
    ensure_app_dirs,
    profiles_dir,
    settings_path,
)


PROFILE_SCHEMA_VERSION = 2
RESPONSE_SPEED_CHOICES = ("fast", "balanced", "patient")
TRIGGER_GUARD_CHOICES = ("off", "standard", "strong")
DEFAULT_SPEECH_SETTINGS = {
    "engine": "vosk",
    "language": "en-US",
    "showConfidence": True,
    "minConfidence": 0.35,
    "minUnrecognizedConfidence": 0.25,
    "commandCooldownMs": 450,
    "recognizedDelayMs": 0,
    "unrecognizedDelayMs": 100,
    "maxAlternatives": 0,
    "responseSpeed": "balanced",
    "triggerGuard": "strong",
}

APP_SETTING_KEYS = {
    "firstRunComplete",
    "modelPath",
    "microphoneId",
    "microphoneIndex",
    "startListeningOnLaunch",
    "startOnLogin",
    "pushToTalkPortalReady",
}
TRIGGER_KINDS = ("keyboard", "device", "screen")


def default_settings() -> dict[str, Any]:
    return {
        "firstRunComplete": False,
        "modelPath": str(default_model_path()),
        "microphoneId": "default",
        "microphoneIndex": None,
        "startListeningOnLaunch": False,
        "startOnLogin": False,
        "pushToTalkPortalReady": False,
    }


def load_settings() -> dict[str, Any]:
    ensure_app_dirs()
    path = settings_path()
    if not path.exists():
        return default_settings()

    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_settings()

    merged = default_settings()
    for key in APP_SETTING_KEYS:
        if key in settings:
            merged[key] = settings[key]
    merged.pop("sherpaKwsModelPath", None)
    merged.pop("speechEngine", None)
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    ensure_app_dirs()
    settings_path().write_text(json.dumps(settings, indent=2), encoding="utf-8")


def default_profile() -> dict[str, Any]:
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "id": str(uuid.uuid4()),
        "name": "My Game Profile",
        "target": {"kind": "manual"},
        "activation": {"mode": "always", "hotkey": "F13"},
        "speech": {
            **DEFAULT_SPEECH_SETTINGS,
            "modelPath": str(default_model_path()),
        },
        "commands": [
            {
                "id": str(uuid.uuid4()),
                "name": "Reload",
                "commandWeight": 0.0,
                "actionExecution": "sequential",
                "phrases": ["reload"],
                "actions": [{"type": "key_press", "key": "R"}],
            }
        ],
    }


def profile_path(profile_id: str) -> Path:
    safe_id = "".join(ch for ch in profile_id if ch.isalnum() or ch in ("-", "_"))
    return profiles_dir() / f"{safe_id}.json"


def load_profiles() -> list[dict[str, Any]]:
    ensure_app_dirs()
    profiles: list[dict[str, Any]] = []

    for path in sorted(profiles_dir().glob("*.json")):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile = migrate_profile(profile)
            validate_profile(profile)
        except Exception:
            continue
        profiles.append(profile)

    if not profiles:
        profile = default_profile()
        save_profile(profile)
        profiles.append(profile)

    return profiles


def save_profile(profile: dict[str, Any]) -> None:
    ensure_app_dirs()
    profile = migrate_profile(profile)
    validate_profile(profile)
    profile_path(profile["id"]).write_text(json.dumps(profile, indent=2), encoding="utf-8")


def delete_profile(profile: dict[str, Any]) -> None:
    path = profile_path(profile["id"])
    if path.exists():
        path.unlink()


def import_profile(path: Path) -> dict[str, Any]:
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile = migrate_profile(profile)
    validate_profile(profile)
    if not profile.get("id"):
        profile["id"] = str(uuid.uuid4())
    save_profile(profile)
    return profile


def export_profile(profile: dict[str, Any], path: Path) -> None:
    profile = migrate_profile(profile)
    validate_profile(profile)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def copy_example_profile(source: Path) -> dict[str, Any]:
    profile = json.loads(source.read_text(encoding="utf-8"))
    profile = migrate_profile(profile)
    profile["id"] = str(uuid.uuid4())
    save_profile(profile)
    return profile


def migrate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile = copy.deepcopy(profile)
    schema_version = profile.get("schemaVersion")
    if schema_version == PROFILE_SCHEMA_VERSION:
        _normalize_v2_profile(profile)
        return profile
    if schema_version != 1:
        return profile

    old_speech = profile.get("speech") if isinstance(profile.get("speech"), dict) else {}
    old_engine = str(old_speech.get("engine") or "vosk")
    old_model_path = str(old_speech.get("modelPath") or "")
    model_path = old_model_path if "vosk-model" in Path(old_model_path).name else ""

    speech = {
        **DEFAULT_SPEECH_SETTINGS,
        "modelPath": model_path or str(default_model_path()),
    }

    profile["schemaVersion"] = PROFILE_SCHEMA_VERSION
    profile["speech"] = speech
    for command in profile.get("commands", []):
        if not isinstance(command, dict):
            continue
        old_confidence = command.pop("confidence", None)
        if old_engine == "sherpa-kws" and old_confidence is not None:
            try:
                command["minConfidence"] = _clamped_float(float(old_confidence) / 100, 0.0, 1.0)
            except (TypeError, ValueError):
                pass
        command.setdefault("commandWeight", 0.0)
        command.setdefault("actionExecution", "sequential")
    _normalize_v2_profile(profile)
    return profile


def _normalize_v2_profile(profile: dict[str, Any]) -> None:
    activation = profile.get("activation")
    if isinstance(activation, dict):
        if activation.get("mode") == "toggle":
            activation["mode"] = "always"
        if activation.get("trigger") not in TRIGGER_KINDS:
            activation["trigger"] = "keyboard"
        device = activation.get("device")
        if device is not None and not _valid_trigger_device(device):
            activation.pop("device", None)

    speech = profile.get("speech")
    if not isinstance(speech, dict):
        speech = {}
    model_path = str(speech.get("modelPath") or default_model_path())
    normalized_speech = {
        **DEFAULT_SPEECH_SETTINGS,
        **speech,
        "engine": "vosk",
        "language": str(speech.get("language") or "en-US"),
        "modelPath": model_path,
    }
    normalized_speech.pop("sherpaKwsModelPath", None)
    if normalized_speech.get("responseSpeed") not in RESPONSE_SPEED_CHOICES:
        normalized_speech["responseSpeed"] = DEFAULT_SPEECH_SETTINGS["responseSpeed"]
    if normalized_speech.get("triggerGuard") not in TRIGGER_GUARD_CHOICES:
        normalized_speech["triggerGuard"] = DEFAULT_SPEECH_SETTINGS["triggerGuard"]
    profile["speech"] = normalized_speech

    for command in profile.get("commands", []):
        if not isinstance(command, dict):
            continue
        command.pop("confidence", None)
        command.setdefault("commandWeight", 0.0)
        if command.get("actionExecution") not in {"sequential", "parallel"}:
            command["actionExecution"] = "sequential"


def validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("schemaVersion") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"Only profile schemaVersion {PROFILE_SCHEMA_VERSION} is supported")
    _require_text(profile, "id")
    _require_text(profile, "name")

    activation = profile.get("activation")
    if not isinstance(activation, dict):
        raise ValueError("Profile must include activation settings")
    if activation.get("mode") not in {"always", "push_to_talk"}:
        raise ValueError("Activation mode must be always or push_to_talk")

    speech = profile.get("speech")
    if not isinstance(speech, dict):
        raise ValueError("Profile must include speech settings")
    if speech.get("engine") != "vosk":
        raise ValueError("Speech engine must be vosk")
    _validate_confidence(speech.get("minConfidence", 0.0), "Minimum confidence")
    _validate_confidence(
        speech.get("minUnrecognizedConfidence", 0.0),
        "Minimum unrecognized confidence",
    )
    if int(speech.get("recognizedDelayMs", 0)) < 0:
        raise ValueError("Recognized speech delay cannot be negative")
    if int(speech.get("unrecognizedDelayMs", 100)) < 0:
        raise ValueError("Unrecognized speech delay cannot be negative")
    command_cooldown = int(speech.get("commandCooldownMs", 450))
    if command_cooldown < 0 or command_cooldown > 5000:
        raise ValueError("Command cooldown must be 0-5000 ms")
    max_alternatives = int(speech.get("maxAlternatives", 0))
    if max_alternatives < 0 or max_alternatives > 10:
        raise ValueError("Max alternatives must be 0-10")
    if speech.get("responseSpeed", "balanced") not in RESPONSE_SPEED_CHOICES:
        raise ValueError("Response speed must be fast, balanced, or patient")
    if speech.get("triggerGuard", "strong") not in TRIGGER_GUARD_CHOICES:
        raise ValueError("Accidental trigger guard must be off, standard, or strong")

    commands = profile.get("commands")
    if not isinstance(commands, list):
        raise ValueError("Profile commands must be a list")

    command_ids: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError("Every command must be an object")
        _require_text(command, "id")
        _require_text(command, "name")
        if command["id"] in command_ids:
            raise ValueError(f"Duplicate command id: {command['id']}")
        command_ids.add(command["id"])

        phrases = command.get("phrases", [])
        if not isinstance(phrases, list):
            raise ValueError(f"Command '{command['name']}' phrases must be a list")
        for phrase in phrases:
            if not isinstance(phrase, str):
                raise ValueError(f"Command '{command['name']}' phrases must be text")

        if "minConfidence" in command and command.get("minConfidence") not in ("", None):
            _validate_confidence(
                command.get("minConfidence"),
                f"Command '{command['name']}' minimum confidence",
            )

        command_weight = float(command.get("commandWeight", 0.0))
        if command_weight < 0 or command_weight > 1:
            raise ValueError(f"Command '{command['name']}' weight must be 0.00-1.00")

        if command.get("actionExecution", "sequential") not in {"sequential", "parallel"}:
            raise ValueError(
                f"Command '{command['name']}' action execution must be sequential or parallel"
            )

        actions = command.get("actions", [])
        if not isinstance(actions, list):
            raise ValueError(f"Command '{command['name']}' actions must be a list")
        for action in actions:
            validate_action(action)

    build_phrase_map(profile)


def validate_action(action: dict[str, Any]) -> None:
    if not isinstance(action, dict):
        raise ValueError("Action must be an object")
    action_type = action.get("type")

    if action_type == "key_press":
        return
    elif action_type == "key_combo":
        keys = action.get("keys")
        if not isinstance(keys, list):
            raise ValueError("key_combo keys must be a list")
        if "durationMs" in action and int(action.get("durationMs", 0)) < 0:
            raise ValueError("key_combo durationMs cannot be negative")
    elif action_type in {"key_hold", "key_release", "key_toggle"}:
        return
    elif action_type == "mouse_click":
        if action.get("button") not in {"", None, "left", "right", "middle", "back", "forward"}:
            raise ValueError("mouse_click button must be left, right, middle, back, or forward")
    elif action_type == "type_text":
        return
    elif action_type == "delay":
        if int(action.get("ms", 0)) < 0:
            raise ValueError("delay ms cannot be negative")
    elif action_type in {"nome_emergency_stop", "nome_stop_listening"}:
        return
    elif action_type == "system_command":
        if not isinstance(action.get("command", ""), str):
            raise ValueError("system_command command must be text")
        if "timeoutMs" in action:
            timeout_ms = int(action.get("timeoutMs", 0))
            if timeout_ms < 0 or timeout_ms > 600000:
                raise ValueError("system_command timeoutMs must be 0-600000 ms")
    else:
        raise ValueError(f"Unsupported action type: {action_type}")


def _valid_trigger_device(device: Any) -> bool:
    if not isinstance(device, dict):
        return False
    hex_id = re.compile(r"[0-9a-f]{4}")
    if not (hex_id.fullmatch(str(device.get("vendor", ""))) and hex_id.fullmatch(str(device.get("product", "")))):
        return False
    return device.get("code") is None or isinstance(device.get("code"), int)


def _require_text(data: dict[str, Any], key: str) -> None:
    if not str(data.get(key, "")).strip():
        raise ValueError(f"Missing required text field: {key}")


def _validate_confidence(value: Any, label: str) -> None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number from 0.00 to 1.00") from None
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"{label} must be from 0.00 to 1.00")


def _clamped_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
