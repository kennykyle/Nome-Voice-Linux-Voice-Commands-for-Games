# V1 Implementation Plan

## Product Goal

V1 should let a disabled gamer create a profile, add their own spoken phrases, map those phrases to game inputs, and use them reliably with low latency on CachyOS GNOME Wayland.

The V1 product should feel like a tool, not a configuration science project.

## Architecture Choice

Use the middle-ground architecture:

- Rust core library for profile parsing, phrase matching, action modeling, and backend interfaces.
- Rust daemon for recognition, listening state, command firing, action execution, and uinput.
- Python GTK/libadwaita GUI for the first-run wizard, profile editor, and accessible controls.
- Local Unix socket IPC between GUI and daemon.

This gives Rust responsibility for the fast/reliable path while keeping GUI iteration easy.

## Non-Goals

- No built-in command phrases.
- No cloud recognition.
- No AI natural language layer.
- No scripting language.
- No variables or conditional logic.
- No profile marketplace.
- No anti-cheat bypass behavior.

## Core User Flows

### First Run

1. GUI starts and launches or connects to `nome-voiced`.
2. Welcome screen explains that Nome Voice needs microphone access, a speech model, and virtual input access.
3. User chooses microphone.
4. GUI asks daemon to check or download the small English Vosk model.
5. GUI asks daemon to check `/dev/uinput` access.
6. If uinput is not ready, the GUI opens guided install instructions or runs the installer with sudo/pkexec.
7. User enters a temporary test phrase.
8. Daemon builds a temporary grammar and reports recognition results.
9. User creates the first profile.

### Create Command

1. Choose or create profile in the GUI.
2. Add command.
3. Type one or more spoken phrases.
4. Add one or more actions.
5. GUI validates duplicate phrases and schema shape.
6. Daemon dry-runs the command when possible.
7. Save profile and activate it.

### Use In Game

1. Select profile manually.
2. Choose activation mode: push-to-talk, toggle listening, or always listening.
3. Big status button shows Listening or Paused.
4. User says a phrase.
5. Daemon recognizes phrase and executes actions through uinput.
6. GUI receives status events for Last heard and Last command fired.

## V1 Actions

- `key_press`: press and release one key.
- `key_combo`: press multiple keys in order, release in reverse order.
- `key_hold`: press and keep one key down.
- `key_release`: release one held key.
- `mouse_click`: click left, right, middle, back, or forward button.
- `type_text`: type literal text.
- `delay`: wait for a number of milliseconds before the next action.

## Activation Modes

- `push_to_talk`: recognition only fires commands while a configured hotkey is held.
- `toggle`: configured hotkey flips between Listening and Paused.
- `always`: recognition stays active while the profile is running.

For V1, global hotkey capture should live in `nome-voiced`, not the GUI, because the daemon already owns evdev/uinput concerns. If global hotkey capture takes too long, the first build can ship with GUI-controlled listening and add global hotkeys as the next task.

## Speech Recognition Strategy

Use Vosk with a restricted grammar generated from the active profile:

```text
profile phrases -> Vosk grammar -> recognized text -> normalized phrase map -> command
```

This keeps recognition fast and avoids accidental open-ended dictation. The daemon should rebuild the grammar whenever the active profile changes.

Phrase normalization should be conservative:

- lowercase
- trim whitespace
- collapse repeated spaces
- strip common punctuation

Do not fuzzy-match aggressively in V1. Users need predictable command firing.

## Input Strategy

Use Linux uinput directly from `nome-voiced`.

Reasons:

- Works under GNOME Wayland because games see a virtual hardware device.
- Avoids X11-only automation.
- Avoids shelling out to `ydotool` for every command.
- Keeps latency low.
- Lets the daemon release held keys on pause, crash recovery paths, and quit.

Installer must configure safe access to `/dev/uinput` with a udev rule and load the `uinput` kernel module.

## IPC Strategy

Use a local Unix domain socket with newline-delimited JSON messages for V1.

Reasons:

- Easy to debug.
- Easy for Python GUI to use.
- Easy for Rust daemon to serve.
- Good enough for low message volume.

The daemon should expose:

- `status.get`
- `profile.load`
- `profile.save`
- `profile.activate`
- `listening.set`
- `command.test`
- `setup.check`
- event stream for `last_heard`, `command_fired`, and backend status.

## Installer Strategy

Provide a guided CachyOS/Arch installer script that:

1. Confirms the system is Arch-like.
2. Installs system packages with pacman:
   - `rust`
   - `cargo`
   - `python`
   - `python-gobject`
   - `gtk4`
   - `libadwaita`
   - `portaudio`
   - `pipewire`
   - `pipewire-pulse`
   - `libpulse`
3. Builds and installs `nome-voiced`.
4. Installs the Python GUI package.
5. Installs the udev rule from `packaging/udev/90-nome-voice-uinput.rules`.
6. Loads the `uinput` module.
7. Downloads the default Vosk model into the user's XDG data directory.
8. Creates a desktop entry.
9. Optionally creates a user systemd unit for the daemon later.

The GUI should also detect missing pieces and point users to the guided installer.

## Milestones

### M0: Contracts

- Profile schema.
- Action model.
- IPC notes.
- Rust/Python split.
- Install requirements.

### M1: Rust Core

- Load profile JSON.
- Validate schema-level requirements in Rust.
- Normalize phrases.
- Reject duplicate normalized phrases.
- Match recognized text to command IDs.
- Unit tests for matching.

### M2: Daemon Dry Run

- Start `nome-voiced`.
- Accept Unix socket client.
- Load and activate profile.
- Emit Listening / Paused status.
- Accept fake recognized text and report matched command.

Initial methods:

- `status.get`
- `setup.check`
- `profile.activate`
- `listening.set`
- `command.test`

### M3: Minimal GUI

- Launch/connect to daemon through `src/nome_voice/ipc.py`.
- Main window.
- Big Listening / Paused button.
- Activation mode selector.
- Last heard.
- Last command fired.
- Profile picker.

### M4: uinput Actions

- uinput diagnostic check.
- Key press/combo/hold/release.
- Mouse click.
- Type text.
- Delay.
- Release held keys on pause and quit.

### M5: Vosk Recognition

- Microphone selection.
- Vosk model loading.
- Restricted grammar from active profile.
- Recognition test.
- Latency check.

### M6: Profile Editor And Import/Export

- Create/edit/delete profiles.
- Add spoken phrases.
- Add/edit/reorder actions.
- Import/export JSON.
- Validate duplicate phrases before save.

### M7: First-Run Wizard And Installer

- Microphone setup.
- Model setup/download.
- uinput setup check.
- CachyOS/Arch guided installer.
- Friendly recovery messages.

## Expansion Points

- Speech backend interface: Vosk first, Rhino or whisper.cpp later.
- Input backend interface: uinput first, GNOME portal/libei later.
- GUI frontend can stay Python or move to Rust later.
- Daemon can become a user systemd service later.
- Profile schema versioning from day one.
