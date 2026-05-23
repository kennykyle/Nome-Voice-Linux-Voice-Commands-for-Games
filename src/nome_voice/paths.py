"""XDG paths used by Nome Voice."""

from __future__ import annotations

import os
from pathlib import Path


APP_ID = "nome-voice"
DEFAULT_MODEL_NAME = "vosk-model-small-en-us-0.15"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_ID


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def default_model_path() -> Path:
    return data_dir() / "models" / DEFAULT_MODEL_NAME


def ensure_app_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    data_dir().mkdir(parents=True, exist_ok=True)
    cache_dir().mkdir(parents=True, exist_ok=True)
    profiles_dir().mkdir(parents=True, exist_ok=True)
