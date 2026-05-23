from nome_voice.profile_store import default_settings, migrate_profile, validate_profile
from nome_voice.matcher import (
    build_phrase_map,
    find_command,
    grammar_phrases,
)
from nome_voice.recognition import (
    alternative_confidence,
    best_final_match,
    result_confidence,
)
from nome_voice.recognizer import parse_vosk_result, vosk_result_debug_summary


def _profile(command: dict | None = None, speech: dict | None = None) -> dict:
    return {
        "schemaVersion": 2,
        "id": "profile",
        "name": "Profile",
        "activation": {"mode": "always", "hotkey": "F13"},
        "speech": {
            "engine": "vosk",
            "language": "en-US",
            "modelPath": "/tmp/vosk-model",
            "showConfidence": True,
            "minConfidence": 0.0,
            "minUnrecognizedConfidence": 0.0,
            "commandCooldownMs": 450,
            "recognizedDelayMs": 0,
            "unrecognizedDelayMs": 100,
            "maxAlternatives": 0,
            **(speech or {}),
        },
        "commands": [
            command
            or {
                "id": "reload",
                "name": "Reload",
                "commandWeight": 0.0,
                "phrases": ["reload"],
                "actions": [{"type": "key_press", "key": "R"}],
            }
        ],
    }


def test_vosk_single_result_uses_average_word_confidence() -> None:
    result = parse_vosk_result(
        {
            "text": "open map",
            "result": [
                {"word": "open", "conf": 0.8},
                {"word": "map", "conf": 0.6},
            ],
        }
    )

    assert result["text"] == "open map"
    assert round(result["alternatives"][0]["confidence"], 2) == 0.70
    assert result["alternatives"][0]["words"][0]["confidence"] == 0.8
    assert round(result_confidence(result), 2) == 0.70


def test_vosk_alternatives_preserve_confidence_and_word_metadata() -> None:
    result = parse_vosk_result(
        {
            "alternatives": [
                {"text": "desktop", "confidence": 0.31},
                {
                    "text": "checkmate",
                    "confidence": 0.88,
                    "result": [{"word": "checkmate", "conf": 0.91}],
                },
            ]
        }
    )

    assert [alternative["text"] for alternative in result["alternatives"]] == [
        "desktop",
        "checkmate",
    ]
    assert result["alternatives"][1]["confidence"] == 0.88
    assert alternative_confidence(result["alternatives"][1]) == 0.91


def test_unbounded_vosk_alternative_score_is_not_treated_as_confidence() -> None:
    result = parse_vosk_result({"alternatives": [{"text": "reload", "confidence": 108.41}]})

    assert result["alternatives"][0]["confidence"] is None
    assert result["alternatives"][0]["score"] == 108.41
    assert alternative_confidence(result["alternatives"][0]) == 0.0
    assert "score=108.41" in vosk_result_debug_summary(result)
    assert "confidence unavailable" in vosk_result_debug_summary(result)


def test_vosk_debug_summary_includes_word_confidence() -> None:
    result = parse_vosk_result(
        {
            "text": "open map",
            "result": [
                {"word": "open", "conf": 0.8},
                {"word": "map", "conf": 0.6},
            ],
        }
    )
    summary = vosk_result_debug_summary(result)

    assert "confidence=0.70" in summary
    assert "open=0.80" in summary
    assert "map=0.60" in summary


def test_best_final_match_uses_highest_confidence_matching_alternative() -> None:
    profile = _profile(
        command={
            "id": "checkmate",
            "name": "Checkmate",
            "commandWeight": 0.0,
            "phrases": ["checkmate"],
            "actions": [{"type": "key_press", "key": "C"}],
        },
        speech={"minConfidence": 0.5},
    )
    result = parse_vosk_result(
        {
            "alternatives": [
                {"text": "desktop", "confidence": 0.95},
                {"text": "checkmate", "confidence": 0.74},
            ]
        }
    )

    match = best_final_match(profile, result, profile["speech"])

    assert match is not None
    assert match["commandName"] == "Checkmate"
    assert match["accepted"] is True
    assert match["alternativeIndex"] == 1


def test_best_final_match_rejects_below_threshold() -> None:
    profile = _profile(speech={"minConfidence": 0.75})
    result = parse_vosk_result({"text": "reload", "result": [{"word": "reload", "conf": 0.5}]})

    match = best_final_match(profile, result, profile["speech"])

    assert match is not None
    assert match["accepted"] is False
    assert match["reason"] == "below threshold"
    assert round(match["rawConfidence"], 2) == 0.50


def test_command_weight_and_override_are_applied() -> None:
    profile = _profile(
        command={
            "id": "reload",
            "name": "Reload",
            "minConfidence": 0.6,
            "commandWeight": 0.2,
            "phrases": ["reload"],
            "actions": [{"type": "key_press", "key": "R"}],
        },
        speech={"minConfidence": 0.9},
    )
    result = parse_vosk_result({"text": "reload", "result": [{"word": "reload", "conf": 0.45}]})

    match = best_final_match(profile, result, profile["speech"])

    assert match is not None
    assert match["accepted"] is True
    assert round(match["effectiveConfidence"], 2) == 0.65
    assert match["threshold"] == 0.6


def test_single_word_command_prefixes_are_not_added_to_grammar() -> None:
    profile = _profile(
        command={
            "id": "desktop",
            "name": "Desktop",
            "commandWeight": 0.0,
            "phrases": ["desktop"],
            "actions": [{"type": "key_press", "key": "D"}],
        }
    )

    phrases = grammar_phrases(profile)

    assert "desktop" in phrases
    assert "des" not in phrases
    assert "desk" not in phrases
    assert find_command(profile, "desk") is None


def test_duplicate_normalized_phrases_are_rejected() -> None:
    profile = _profile()
    profile["commands"] = [
        {
            "id": "desktop",
            "name": "Desktop",
            "commandWeight": 0.0,
            "phrases": ["open map"],
            "actions": [{"type": "key_press", "key": "D"}],
        },
        {
            "id": "desk",
            "name": "Desk",
            "commandWeight": 0.0,
            "phrases": ["Open, map!"],
            "actions": [{"type": "key_press", "key": "K"}],
        },
    ]

    try:
        build_phrase_map(profile)
    except ValueError as exc:
        assert "open map" in str(exc)
    else:
        raise AssertionError("Expected duplicate phrase collision to be rejected")


def test_single_letter_commands_gain_spoken_letter_aliases() -> None:
    profile = _profile(
        command={
            "id": "c",
            "name": "C",
            "commandWeight": 0.0,
            "phrases": ["C"],
            "actions": [{"type": "key_press", "key": "C"}],
        }
    )

    phrases = grammar_phrases(profile)

    assert "c" in phrases
    assert "see" in phrases
    assert "sea" in phrases
    assert "cee" in phrases
    assert find_command(profile, "see")["name"] == "C"


def test_letter_alias_collisions_are_rejected() -> None:
    profile = _profile()
    profile["commands"] = [
        {
            "id": "c",
            "name": "C",
            "commandWeight": 0.0,
            "phrases": ["C"],
            "actions": [{"type": "key_press", "key": "C"}],
        },
        {
            "id": "see",
            "name": "See",
            "commandWeight": 0.0,
            "phrases": ["see"],
            "actions": [{"type": "key_press", "key": "S"}],
        },
    ]

    try:
        build_phrase_map(profile)
    except ValueError as exc:
        assert "see" in str(exc)
    else:
        raise AssertionError("Expected alias collision to be rejected")


def test_legacy_keyword_profile_migrates_command_confidence_to_vosk_threshold() -> None:
    migrated = migrate_profile(
        {
            "schemaVersion": 1,
            "id": "legacy",
            "name": "Legacy",
            "activation": {"mode": "always", "hotkey": "F13"},
            "speech": {"engine": "sherpa-kws"},
            "commands": [
                {
                    "id": "reload",
                    "name": "Reload",
                    "confidence": 70,
                    "phrases": ["reload"],
                    "actions": [{"type": "key_press", "key": "R"}],
                }
            ],
        }
    )

    validate_profile(migrated)
    assert migrated["schemaVersion"] == 2
    assert migrated["speech"]["engine"] == "vosk"
    assert migrated["commands"][0]["minConfidence"] == 0.7
    assert "confidence" not in migrated["commands"][0]


def test_legacy_vosk_profile_drops_command_confidence() -> None:
    migrated = migrate_profile(
        {
            "schemaVersion": 1,
            "id": "legacy",
            "name": "Legacy",
            "activation": {"mode": "always", "hotkey": "F13"},
            "speech": {"engine": "vosk"},
            "commands": [
                {
                    "id": "reload",
                    "name": "Reload",
                    "confidence": 70,
                    "phrases": ["reload"],
                    "actions": [{"type": "key_press", "key": "R"}],
                }
            ],
        }
    )

    validate_profile(migrated)
    assert "confidence" not in migrated["commands"][0]
    assert "minConfidence" not in migrated["commands"][0]
    assert migrated["speech"]["minConfidence"] == 0.35


def test_toggle_activation_migrates_to_always() -> None:
    profile = _profile()
    profile["activation"]["mode"] = "toggle"

    migrated = migrate_profile(profile)

    validate_profile(migrated)
    assert migrated["activation"]["mode"] == "always"


def test_command_action_execution_defaults_to_sequential() -> None:
    migrated = migrate_profile(_profile())

    validate_profile(migrated)
    assert migrated["commands"][0]["actionExecution"] == "sequential"


def test_command_action_execution_accepts_parallel() -> None:
    profile = _profile()
    profile["commands"][0]["actionExecution"] = "parallel"

    validate_profile(profile)


def test_app_startup_settings_default_to_disabled() -> None:
    settings = default_settings()

    assert settings["startListeningOnLaunch"] is False
    assert settings["startOnLogin"] is False
