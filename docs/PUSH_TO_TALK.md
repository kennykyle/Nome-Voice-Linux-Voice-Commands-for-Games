# Push-To-Talk

Switch **Activation Mode** to **Push To Talk** and choose what you hold in
**Push-To-Talk Button**. In Always Listening mode, the same button switches listening on
and off.

| Button | Works inside games | Setup |
|---|---|---|
| Keyboard key | Yes | Pick the key, then click **Make It Work In Games**. GNOME asks you to confirm the key once. No extra permissions. |
| Mouse button or foot pedal | Yes | Choose the device, click **Allow Access** (password prompt, once), then **Detect Button** and press it. |
| On-screen button only | No | Hold the big button in the Nome Voice window. |

How it behaves:

- Nome Voice keeps the recognizer ready while you are not talking, and includes the last
  0.3 s of audio from before you pressed, so the first word isn't clipped.
- When you let go, it listens another 0.15 s and then finishes the phrase you were
  saying. You don't have to keep holding after you finish speaking.
- Commands can also fire while you are still holding, as soon as a complete command is
  heard.

## Allow Access

Linux only lets the desktop read input devices directly. **Allow Access** installs a
udev rule for the one device you chose (`/etc/udev/rules.d/70-nome-voice-ptt-VENDOR-PRODUCT.rules`)
that gives the logged-in user read access to it. Many pedals and gaming mice also have a
keyboard part; Nome Voice warns you in that case. Don't do this for the keyboard you
type passwords on: use the Keyboard key option for keyboards. To undo, delete the rule
file.

## Driving Nome Voice From Other Programs

While Nome Voice is running, other programs can trigger these actions:

```bash
gapplication action local.nomevoice.NomeVoice emergency-stop
gapplication action local.nomevoice.NomeVoice toggle-listening
gapplication action local.nomevoice.NomeVoice ptt-press
gapplication action local.nomevoice.NomeVoice ptt-release
```

For example, add a GNOME custom keyboard shortcut (Settings > Keyboard > Custom
Shortcuts) that runs the `emergency-stop` command, or call `ptt-press` and `ptt-release`
from an on-screen keyboard.
