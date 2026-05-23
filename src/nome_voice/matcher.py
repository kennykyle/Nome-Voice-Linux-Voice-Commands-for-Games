"""Phrase normalization and final command matching."""

from __future__ import annotations

import re
from typing import Any

LETTER_SPOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "a": ("ay",),
    "b": ("bee",),
    "c": ("see", "sea", "cee"),
    "d": ("dee",),
    "e": ("ee",),
    "f": ("eff",),
    "g": ("gee",),
    "h": ("aitch",),
    "i": ("eye",),
    "j": ("jay",),
    "k": ("kay",),
    "l": ("ell",),
    "m": ("em",),
    "n": ("en",),
    "o": ("oh",),
    "p": ("pee", "pea"),
    "q": ("cue", "queue"),
    "r": ("are", "ar"),
    "s": ("ess",),
    "t": ("tee", "tea"),
    "u": ("you",),
    "v": ("vee",),
    "w": ("double you", "double u"),
    "x": ("ex",),
    "y": ("why", "wye"),
    "z": ("zee", "zed"),
}


def normalize_phrase(text: str) -> str:
    """Normalize user phrases and final Vosk text to the same comparison form."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def build_phrase_map(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return every final-recognition phrase mapped to exactly one command.

    Phrase prefixes are intentionally not generated. Final recognition should only fire commands
    for complete user-authored phrases, plus explicit spoken-letter aliases for one-letter commands.
    """
    phrase_map: dict[str, dict[str, Any]] = {}

    for command in profile.get("commands", []):
        seen_for_command: set[str] = set()
        for phrase in command.get("phrases", []):
            normalized = normalize_phrase(str(phrase))
            if not normalized:
                continue
            _add_phrase_mapping(phrase_map, seen_for_command, normalized, command)
            for alias in _phrase_aliases(normalized):
                _add_phrase_mapping(phrase_map, seen_for_command, alias, command)

    return phrase_map


def _add_phrase_mapping(
    phrase_map: dict[str, dict[str, Any]],
    seen_for_command: set[str],
    normalized: str,
    command: dict[str, Any],
) -> None:
    if normalized in seen_for_command:
        return
    if normalized in phrase_map:
        existing = phrase_map[normalized]
        if existing is command or existing.get("id") == command.get("id"):
            seen_for_command.add(normalized)
            return
        other = existing.get("name", "another command")
        raise ValueError(
            f"Phrase '{normalized}' is used by both '{other}' and '{command.get('name')}'"
        )
    seen_for_command.add(normalized)
    phrase_map[normalized] = command


def _phrase_aliases(normalized: str) -> tuple[str, ...]:
    if len(normalized) == 1 and normalized.isalpha():
        return tuple(
            dict.fromkeys(
                alias
                for alias in LETTER_SPOKEN_ALIASES.get(normalized, ())
                if alias and alias != normalized
            )
        )
    return ()


def grammar_phrases(profile: dict[str, Any]) -> list[str]:
    """Return the restricted Vosk grammar for final recognition."""
    phrases = sorted(build_phrase_map(profile).keys())
    if "[unk]" not in phrases:
        phrases.append("[unk]")
    return phrases


def find_command(profile: dict[str, Any], recognized_text: str) -> dict[str, Any] | None:
    """Find the command for a complete final-recognition result."""
    return build_phrase_map(profile).get(normalize_phrase(recognized_text))
