# Profile Schema Notes

Profiles are plain JSON so users can back them up, share them, and recover them if the GUI is not available.

The current schema file is `schemas/profile.v2.schema.json`. Version 1 profiles are migrated on load.

## Minimal Example

```json
{
  "schemaVersion": 2,
  "id": "018f4f3d-8db5-7f0a-9e1d-111111111111",
  "name": "Example Game",
  "activation": {
    "mode": "push_to_talk",
    "hotkey": "F13"
  },
  "speech": {
    "engine": "vosk",
    "language": "en-US",
    "modelPath": "~/.local/share/nome-voice/models/vosk-model-small-en-us-0.15",
    "showConfidence": true,
    "minConfidence": 0.35,
    "minUnrecognizedConfidence": 0.25,
    "commandCooldownMs": 450,
    "recognizedDelayMs": 0,
    "unrecognizedDelayMs": 100,
    "maxAlternatives": 0
  },
  "commands": [
    {
      "id": "018f4f3d-8db5-7f0a-9e1d-222222222222",
      "name": "Reload",
      "commandWeight": 0.0,
      "phrases": ["reload"],
      "actions": [
        {
          "type": "key_press",
          "key": "R"
        }
      ]
    }
  ]
}
```

## Recognition Fields

- `minConfidence` is a raw Vosk confidence threshold from `0.00` to `1.00`.
- `minUnrecognizedConfidence` filters low-confidence non-command speech in logs.
- `commandCooldownMs` prevents the same accepted command from firing again too quickly.
- `recognizedDelayMs` suppresses any new command execution after a command fires.
- `unrecognizedDelayMs` suppresses command execution briefly after rejected or unmatched speech.
- `maxAlternatives` controls how many final Vosk alternatives are evaluated. `0` is fastest and preserves calibrated word confidence.
- Command `minConfidence` overrides the profile threshold for that command.
- Command `commandWeight` is an app-side confidence bonus applied after a phrase matches.

## Command Matching

Every phrase belongs to exactly one command inside a profile. Duplicate normalized
phrases are rejected. Matching is based on complete final Vosk results; prefix matches
are not generated automatically.

## Action Semantics

- Actions run in list order.
- `delay` pauses before the next action.
- `key_hold` stays active until a matching `key_release`, profile pause, or app quit.
- `type_text` should emit characters through the input backend, not through clipboard paste.
- Nome Voice actions live in their own editor category. `nome_emergency_stop` cancels active recognition and releases held inputs; `nome_stop_listening` pauses listening.
- System actions live in their own editor category. `system_command` runs a user-defined Linux command such as `systemctl poweroff` or `systemctl reboot`. The editor exposes those as presets rather than hard-coded action types.

## Versioning

`schemaVersion` is required. The app writes v2 profiles and migrates older profiles when possible.
