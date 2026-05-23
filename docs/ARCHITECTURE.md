# Architecture

## V1 Shape

V1 is a split desktop app:

```text
GTK/libadwaita GUI, Python
  |
  | local Unix socket, JSON messages
  v
nome-voiced, Rust
  |
  +-- nome-voice-core
  +-- speech backend: Vosk
  +-- input backend: uinput
```

Rust owns the path from recognized speech to game-visible input. The GUI owns setup, editing, import/export, and accessible controls.

## Rust Workspace

### `crates/nome-voice-core`

Pure Rust library with no GUI/audio/input side effects.

Responsibilities:

- Profile structs.
- Action structs.
- Profile loading and serialization.
- Phrase normalization.
- Duplicate phrase detection.
- Command matching.
- Backend traits for speech and input.
- Unit tests for profile and matcher behavior.

### `crates/nome-voiced`

Rust daemon binary.

Responsibilities:

- Manage active profile.
- Manage listening state.
- Serve local IPC.
- Capture audio.
- Run Vosk recognizer.
- Execute actions through uinput.
- Track held keys.
- Release held keys on pause and quit.
- Emit status events to GUI clients.
- Report setup diagnostics.

## Python Frontend

### `src/nome_voice`

GTK4/libadwaita GUI through PyGObject.

Responsibilities:

- First-run wizard.
- Main Listening / Paused screen.
- Profile picker.
- Profile editor.
- Command editor.
- Action editor.
- Import/export.
- Setup diagnostics UI.
- Accessibility labels and keyboard navigation.

The GUI should not execute game input directly. It asks `nome-voiced` to validate, activate, test, and run commands.

## IPC

Use newline-delimited JSON over a Unix domain socket for V1.

Suggested socket path:

```text
$XDG_RUNTIME_DIR/nome-voice/nome-voiced.sock
```

Message shape:

```json
{
  "id": "request-id",
  "method": "status.get",
  "params": {}
}
```

Response shape:

```json
{
  "id": "request-id",
  "ok": true,
  "result": {}
}
```

Event shape:

```json
{
  "event": "last_heard",
  "data": {
    "text": "reload"
  }
}
```

Keep the protocol boring and explicit. It can become DBus later if needed.

## Daemon State Model

`nome-voiced` owns:

- active profile
- activation mode
- listening state
- last heard text
- last command fired
- current speech backend status
- current input backend status
- currently held keys

The GUI renders this state but does not treat itself as the source of truth.

## Threading Model

- IPC server runs on async tasks.
- Audio capture runs in a dedicated worker thread or async-compatible stream callback.
- Final speech recognition runs outside the IPC request path.
- Action execution runs in a queued worker so `delay` does not block recognition or IPC.
- Status events are broadcast to connected GUI clients.

## Failure Rules

- If speech model is missing, the daemon starts and reports `speech_unavailable`.
- If uinput is missing, the daemon starts and reports `input_unavailable`.
- If input is unavailable, profile editing and recognition testing still work.
- On pause, release all held keys by default.
- On quit, release all held keys.
- If the GUI crashes, the daemon should keep listening only if the user explicitly enabled that behavior later. V1 can stop listening when the GUI disconnects.

## Data Locations

Use XDG paths on Linux:

- Config: `~/.config/nome-voice/`
- Data: `~/.local/share/nome-voice/`
- Cache: `~/.cache/nome-voice/`
- Runtime socket: `$XDG_RUNTIME_DIR/nome-voice/nome-voiced.sock`

Suggested files:

```text
~/.config/nome-voice/settings.json
~/.config/nome-voice/profiles/*.json
~/.local/share/nome-voice/models/vosk-model-small-en-us-0.15/
```
