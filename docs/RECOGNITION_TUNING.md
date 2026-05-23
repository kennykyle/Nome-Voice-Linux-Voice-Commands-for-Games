# Recognition Tuning

Nome Voice fires commands from Vosk final results only. The recognizer uses a restricted
grammar generated from the active profile phrases plus `[unk]` for non-command speech.
It does not generate prefix phrases automatically.

## Speed Defaults

- `maxAlternatives: 0` is the fastest path and keeps word-level confidence calibrated.
- Vosk word timing is enabled so final results include confidence whenever the model provides it.
- The recognizer uses short endpointer delays so speech finalizes quickly after the user stops talking.

## Accidental Trigger Controls

- Prefer two-word phrases for commands that could appear in conversation.
- Raise profile `minConfidence` when many commands should require cleaner recognition.
- Use command `minConfidence` for risky commands such as short words, destructive actions, or common chat terms.
- Use `commandWeight` only for phrases that Vosk recognizes consistently but scores too harshly.
- Raise `commandCooldownMs` when the same command fires twice from one utterance.
- Keep `recognizedDelayMs` low for responsiveness; use it only when any command should
  pause the whole recognizer briefly after firing.
- Keep `unrecognizedDelayMs` modest so rejected speech does not block the next intentional command for long.

## Logging

The log records the final text, raw confidence, effective confidence, threshold, command
weight, selected alternative, and rejection reason. When `maxAlternatives` is greater
than `0`, Vosk may provide an unbounded score instead of calibrated confidence for some
alternatives; those scores are logged but are not treated as confidence.
