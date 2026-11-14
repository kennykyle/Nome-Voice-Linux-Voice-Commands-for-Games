# Recognition Tuning

Nome Voice fires a command when a complete phrase is heard. The recognizer uses a
restricted grammar built from the active profile phrases, plus guard words and `[unk]`
for everything else. It does not generate prefix phrases automatically.

## Response Speed

Two Vosk recognizers hear the same audio:

- The **quick path** fires as soon as you have been quiet for the response-speed delay,
  but only if what you said is a complete command that no longer command starts with.
- The **main path** uses Vosk's normal end-of-speech detection. It catches phrases said
  with pauses, commands that are the start of a longer command (`reload` when the profile
  also has `reload all`), and noisy rooms where silence can't be detected.

Whichever path fires resets the other, so one utterance fires at most one command.

| Setting  | Quick path fires after | Main path                          |
|----------|------------------------|------------------------------------|
| Fast     | 150 ms of quiet        | model default (about 0.5 s)        |
| Balanced | 250 ms of quiet        | model default (about 0.5 s)        |
| Patient  | 400 ms of quiet        | waits about 1 s, for paused speech |

Faster settings can fire if you say a command word, pause, and keep talking. Patient is
the best choice for slow speech or pauses for breath inside a phrase.

Vosk's Python package (0.3.45, the newest release) has no API for end-of-speech timing,
so Patient builds a copy of the model folder out of symlinks with a rewritten
`conf/model.conf` under `~/.cache/nome-voice/model-variants/`.

## Accidental Trigger Guard

With only a few command phrases in the grammar, Vosk must explain every sound with one
of them, and it reports near-100% confidence even when it is wrong. So "the system is
updating" becomes `system update`, and the confidence threshold cannot catch it.

The guard gives the recognizer other words to choose from. Those words never fire a
command:

- **Standard** adds word forms of your command words, such as `reloading`, `terminals`,
  and `updates`.
- **Strong** adds about 1,400 everyday words on top of that.

Words that could steal a command are never added: the command words themselves, their
homophones (`for` and `four`), near spellings (`pseudo` and `psuedo`), and pieces of
command words (`desk` and `top` for `desktop`, `up` and `date` for `update`).

In offline tests with synthetic voices, Strong cut accidental triggers from everyday
phrases by about 60% without losing real commands. If real commands start getting missed
with your voice, switch to Standard.

## Accidental Trigger Controls

- Prefer two-word phrases for commands that could appear in conversation.
- Keep the guard on Strong when listening all the time near conversation, videos, or
  game audio.
- Raise `commandCooldownMs` if the same command fires twice from one utterance.
- Use command `minConfidence` and `commandWeight` sparingly. With a small grammar, Vosk
  confidence is close to 1.0 for most matches.

## Logging

The log records the final text, confidence, threshold, and rejection reason for each
result. For fired commands it also shows which path decided (`quick path` or `main path`),
how long after you stopped speaking it fired, and how long it took to send the keys. When
the speech model doesn't know a word in your phrases, such as `sudo`, the log names it,
because phrases with unknown words can never fire.
