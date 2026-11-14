# Nome Voice

Nome Voice is a local voice-command app for CachyOS GNOME 50+.

Say a phrase like `reload`, `open map`, or `pause listening`, and Nome Voice can press
keys, click the mouse, type text, wait, run a small action sequence, or trigger a
system command for you.

It is built for disabled gamers first, but it is also meant to be friendly for casual
players, streamers, Linux tinkerers, and anyone who wants simple voice macros without
sending their audio to the cloud.

## Why This Exists

I have SMA type 2, and gaming on Linux well was not fully possible until now. Sometimes the problem is not the game itself, but the pile of little
inputs around it: reload, map, push to talk, inventory, menus, shortcuts, launchers,
and desktop controls.

I wanted something in the spirit of Voice Attack: say a phrase, fire an action, all seamlessly without distracting from the action. The Linux side needed something that felt local, practical, and built
with disability in mind from the start.

That is why Nome Voice exists.

This project also sits next to my other accessibility tool,
[Nome On-Screen Keyboard](https://github.com/kennykyle/Nome-Linux-On-Screen-Keyboard).
The bigger goal is simple: make Linux gaming and everyday computer use less dependent
on perfect hands, perfect stamina, online services or perfect hardware setups.

## Who It Is For

- Disabled gamers who want fewer physical inputs between them and the game.
- Casual Linux players who want quick voice shortcuts or smart pc.
- Streamers who want local commands without cloud voice services.
- People who play on GNOME/Wayland, including Proton games.
- Accessibility tinkerers who like plain JSON profiles and local tools.

## What You Can Do With It

- Make different profiles for different games or apps.
- Add spoken phrases like `reload`, `open map`, `push to talk`, or `emergency stop`.
- Turn phrases into key presses, key combos, held keys, released keys, or toggled keys.
- Click mouse buttons by voice.
- Type short pieces of text.
- Add delays between actions.
- Run action steps one after another or in parallel.
- Pause listening with a voice command.
- Stop everything instantly with Emergency Stop: the big button, a voice command, or a
  global key that works inside games. It cancels running macros and lets go of held keys.
- Choose always-listening mode or push-to-talk mode.
- Hold a keyboard key, a mouse button, a foot pedal, or the on-screen button to talk.
  Push-to-talk works inside games.
- Choose how fast commands fire, from about 0.15 to 0.4 seconds after you stop talking.
- Keep everyday talk, videos, and chat from firing commands with the Accidental Trigger Guard.
- Use an echo-cancelled microphone so game audio from your speakers can't trigger commands.
- Tune confidence, command weights, and cooldowns.
- Keep a microphone meter visible even while recognition is paused.
- Start listening when the app opens.
- Start the app automatically when you log into GNOME.
- Import and export profiles as JSON.
- Control Nome Voice from shortcuts or other apps, like an on-screen keyboard.

## A Few Examples

You can make commands like these:

```text
"reload"        -> press R
"open map"      -> press Ctrl+M
"hold sprint"   -> hold Shift
"stop sprint"   -> release Shift
"push to talk"  -> hold your chat key
"be right back" -> type a short chat message
"panic"         -> emergency stop and release held inputs
```

The idea is not to replace your whole keyboard. It is to take the annoying, repeated,
or physically expensive inputs and make them easier.

## How It Works

Nome Voice listens locally with Vosk, an offline speech recognizer. Your command phrases
come from the active profile, so the recognizer focuses on the words you actually want
to use instead of doing open-ended dictation.

When Nome Voice hears a complete phrase match, it runs the actions attached to that
command. For game-visible input, it uses Linux virtual input through `uinput`.

Commands fire about a quarter of a second after you stop talking. Response Speed in
Settings > Recognition sets this: Fast, Balanced, or Patient. Patient also waits through
pauses for breath in the middle of a phrase.

Nome Voice also listens for everyday words and word forms like "reloading", so ordinary
talk near the microphone is far less likely to be mistaken for a command. This is the
Accidental Trigger Guard in Settings > Recognition. If real commands start getting
missed, set it to Standard.

Audio recognition stays on your machine. No cloud speech service is required.

## Push-To-Talk

Set Activation Mode to Push To Talk and choose what you hold:

- **Keyboard key**: pick the key, then click **Make It Work In Games**. GNOME asks you to
  confirm it once.
- **Mouse button or foot pedal**: choose the device, click **Allow Access** (asks for your
  password once), then **Detect Button** and press it.
- **On-screen button**: hold the big button in the Nome Voice window.

You don't have to keep holding after you finish speaking. Nome Voice finishes the phrase
after you let go, and it keeps the moment before you pressed, so your first word isn't
cut off. More detail is in `docs/PUSH_TO_TALK.md`.

## Emergency Stop From Anywhere

The Emergency Stop button, the voice action, and this command all stop listening, cancel
running macros, and let go of held keys right away:

```bash
gapplication action local.nomevoice.NomeVoice emergency-stop
```

To put it on a key that works even inside games, open GNOME Settings > Keyboard >
View and Customize Shortcuts > Custom Shortcuts, add that command, and set a key such as
Pause. On-screen keyboards and other apps can also use `toggle-listening`, `ptt-press`,
and `ptt-release` the same way.

## Echo Cancelling For Speakers

If you play with speakers, the microphone also hears the game. Turn on an
echo-cancelled microphone:

```bash
bash scripts/setup-echo-cancel.sh
```

Then pick **Microphone, echo cancelled (Nome Voice)** in Settings > Setup > Microphone.
It follows whatever you are using, speakers or headset, and doesn't change the
microphone other apps use. More detail is in `docs/ECHO_CANCEL.md`.

## Current Support

Nome Voice is early and currently targets this setup:

- Linux
- GNOME
- Wayland
- CachyOS / Arch-based systems

Other desktop environments are not supported yet. KDE Plasma support is on the roadmap.

## Screenshots

Real screenshots can replace these placeholders later.

![Nome Voice main screen placeholder](docs/images/main-screen-placeholder.svg)

![Nome Voice settings placeholder](docs/images/settings-placeholder.svg)

![Nome Voice command editor placeholder](docs/images/command-editor-placeholder.svg)

## Install On CachyOS / Arch

From the repo root:

```bash
bash setup.sh
```

The setup guide checks system packages, `uinput` access, the Vosk model, the desktop
launcher, and the local command-line entry point.

Useful skips:

```bash
bash setup.sh --skip-uinput
bash setup.sh --skip-model
bash setup.sh --skip-gui
bash setup.sh --skip-rust
```

To update, download the new version and run `bash setup.sh` again. Your profiles and
settings are kept.

## Run

After setup:

```bash
nome-voice
```

Or open `Nome Voice` from the GNOME app list.

## Debugging

Show the GUI launch log:

```bash
bash scripts/show-gui-log.sh
```

Check the daemon service:

```bash
systemctl --user status nome-voiced.service
```

Test the daemon socket:

```bash
bash scripts/test-daemon.sh
```

## Project Layout

```text
src/nome_voice/             Python GTK app
crates/nome-voice-core/     Rust profile and matcher core
crates/nome-voiced/         Rust input daemon (virtual keyboard and mouse)
docs/                       Architecture and tuning notes
examples/                   Example profiles
schemas/                    Profile JSON schema
scripts/                    Setup and debug helpers
setup.sh                    Main installer wrapper
packaging/                  Linux packaging notes and udev rules
packaging/icons/            Desktop icon assets
packaging/pipewire/         Echo-cancelled microphone setup
```

## Notes For Tinkerers

Profiles are plain JSON so they can be backed up, shared, edited, and recovered if the
GUI is not available.

Nome Voice stores config under:

```text
~/.config/nome-voice/
```

It stores app data under:

```text
~/.local/share/nome-voice/
```

The startup toggle writes a per-user GNOME autostart entry at:

```text
~/.config/autostart/local.nomevoice.NomeVoice.desktop
```

More detail lives in:

- `docs/ARCHITECTURE.md`
- `docs/PROFILE_SCHEMA.md`
- `docs/RECOGNITION_TUNING.md`
- `docs/PUSH_TO_TALK.md`
- `docs/ECHO_CANCEL.md`
- `examples/profile.example.json`

## Roadmap

- KDE Plasma support.
- Dictation with Whisper.
- Key and button macro improvements.
- Custom sound execution.
- More system presets, such as volume controls and input blocking.
- Screen-area click targets for saved points or regions.
- Basic scripting experiments.
- Maybe local-only agent or LLM interaction.

## Status

Nome Voice is still early, but the goal is steady: a practical Linux voice macro tool
that gives disabled gamers more ways to play while staying simple enough for anyone to
pick up, customize, and understand.

## License

Nome Voice is free software under the GNU General Public License v3.0. See `LICENSE`.
