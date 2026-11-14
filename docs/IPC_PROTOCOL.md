# IPC Protocol

V1 uses newline-delimited JSON over a Unix domain socket.

Suggested socket path:

```text
$XDG_RUNTIME_DIR/nome-voice/nome-voiced.sock
```

## Request

```json
{
  "id": "request-id",
  "method": "status.get",
  "params": {}
}
```

## Response

```json
{
  "id": "request-id",
  "ok": true,
  "result": {
    "listening": false,
    "lastHeard": null,
    "lastCommandFired": null
  }
}
```

## Error Response

```json
{
  "id": "request-id",
  "ok": false,
  "error": {
    "code": "input_unavailable",
    "message": "Nome Voice cannot access /dev/uinput."
  }
}
```

## Event

```json
{
  "event": "command_fired",
  "data": {
    "commandId": "example-command-reload",
    "commandName": "Reload",
    "phrase": "reload"
  }
}
```

## V1 Methods

- `status.get`
- `setup.check`
- `profile.activate`
- `listening.set`
- `command.test`
- `speech.test.start`
- `speech.test.stop`

Implemented dry-run methods:

- `status.get`
- `setup.check`
- `profile.activate`
- `listening.set`
- `command.test`

## V1 Events

- `status.changed`
- `last_heard`
- `command_fired`
- `speech.status`
- `input.status`

## Running Actions

- `actions.execute` runs a list of actions in order and answers when they finish:
  `{"executedActionCount": 3, "cancelled": false}`. Sequences run one at a time.
- `actions.releaseAll` is the emergency stop. It cancels the running sequence and any
  waiting ones within a few milliseconds, releases every key the daemon holds down
  (including keys held partway through a combo), and answers right away. Cancelled
  sequences answer `{"cancelled": true}`.
- Error code `input_unavailable` means the daemon could not open `/dev/uinput`. Other
  errors such as `input_error` (for example an unknown key name) mean the daemon received
  the actions and refused them, so the client must not run them again another way.
- `status.get` stays responsive while a sequence runs and reports `heldKeys`.
- `NOME_VOICED_DRY_RUN=1` makes the daemon accept actions without sending any input
  events, for testing. `status.get` then reports `"dryRun": true`.
