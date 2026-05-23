"""Pure recognition scoring helpers for final Vosk results."""

from __future__ import annotations

from typing import Any

from .matcher import find_command, normalize_phrase


def best_final_match(
    profile: dict[str, Any],
    result: dict[str, Any],
    speech_settings: dict[str, Any],
) -> dict[str, Any] | None:
    alternatives = result.get("alternatives")
    if not isinstance(alternatives, list):
        alternatives = []

    best_accepted: dict[str, Any] | None = None
    best_rejected: dict[str, Any] | None = None
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        text = str(alternative.get("text", "")).strip()
        if not text:
            continue
        command = find_command(profile, text)
        if command is None:
            continue

        raw_confidence = alternative_confidence(alternative)
        command_weight = _command_weight(command)
        effective_confidence = min(1.0, raw_confidence + command_weight)
        threshold = command_threshold(command, speech_settings)
        accepted = effective_confidence >= threshold
        match = {
            "text": text,
            "alternativeIndex": int(alternative.get("index", 0)),
            "alternativeScore": alternative.get("score"),
            "command": command,
            "commandName": command.get("name", "Unnamed Command"),
            "matchedPhrase": _matched_phrase(command, text),
            "rawConfidence": raw_confidence,
            "effectiveConfidence": effective_confidence,
            "threshold": threshold,
            "commandWeight": command_weight,
            "accepted": accepted,
            "reason": "accepted" if accepted else "below threshold",
        }
        if accepted:
            best_accepted = _prefer_match(best_accepted, match)
        else:
            best_rejected = _prefer_match(best_rejected, match)

    return best_accepted or best_rejected


def result_text(result: dict[str, Any]) -> str:
    return str(result.get("text", "")).strip()


def result_confidence(result: dict[str, Any]) -> float:
    alternatives = result.get("alternatives")
    if isinstance(alternatives, list) and alternatives:
        first = alternatives[0]
        if isinstance(first, dict):
            return alternative_confidence(first)
    return 0.0


def command_threshold(command: dict[str, Any], speech_settings: dict[str, Any]) -> float:
    if command.get("minConfidence") not in ("", None):
        return _clamp_float(command.get("minConfidence"), 0.0, 1.0, 0.0)
    return _clamp_float(speech_settings.get("minConfidence"), 0.0, 1.0, 0.0)


def alternative_confidence(alternative: dict[str, Any]) -> float:
    words = alternative.get("words")
    if isinstance(words, list):
        confidences = [
            _clamp_float(word.get("confidence"), 0.0, 1.0, 0.0)
            for word in words
            if isinstance(word, dict) and word.get("confidence") is not None
        ]
        if confidences:
            return sum(confidences) / len(confidences)
    return _clamp_float(alternative.get("confidence"), 0.0, 1.0, 0.0)


def _matched_phrase(command: dict[str, Any], text: str) -> str:
    normalized_text = normalize_phrase(text)
    for phrase in command.get("phrases", []):
        phrase_text = str(phrase)
        if normalize_phrase(phrase_text) == normalized_text:
            return phrase_text
    return text


def _command_weight(command: dict[str, Any]) -> float:
    return _clamp_float(command.get("commandWeight"), 0.0, 1.0, 0.0)


def _prefer_match(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if current is None:
        return candidate
    candidate_score = (
        float(candidate.get("effectiveConfidence", 0.0)),
        float(candidate.get("rawConfidence", 0.0)),
        -int(candidate.get("alternativeIndex", 0)),
    )
    current_score = (
        float(current.get("effectiveConfidence", 0.0)),
        float(current.get("rawConfidence", 0.0)),
        -int(current.get("alternativeIndex", 0)),
    )
    return candidate if candidate_score > current_score else current


def _clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))
