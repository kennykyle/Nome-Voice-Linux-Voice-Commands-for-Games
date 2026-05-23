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
