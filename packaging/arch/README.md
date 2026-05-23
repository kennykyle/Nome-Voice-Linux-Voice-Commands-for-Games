# CachyOS / Arch Packaging Notes

V1 should install as a native Linux desktop app, not as a Flatpak-only app, because uinput access is required for game-visible keyboard and mouse output.

## System Dependencies

Likely pacman packages:

```text
base-devel
rust
cargo
python
python-pip
python-gobject
gtk4
libadwaita
portaudio
pipewire
pipewire-pulse
libpulse
pkgconf
curl
unzip
```

The Python GUI should rely mostly on distro GTK bindings and the standard library. Rust owns recognition, input, and action execution.

## Installed Pieces

Expected install targets:

```text
/usr/bin/nome-voiced
/usr/bin/nome-voice
/usr/share/applications/local.nomevoice.NomeVoice.desktop
/usr/share/icons/hicolor/scalable/apps/nome-voice.svg
/usr/share/nome-voice/
/etc/udev/rules.d/90-nome-voice-uinput.rules
```

V1 can launch `nome-voiced` from the GUI. A user systemd unit can be added later when background operation is polished.

## uinput Setup

Install `packaging/udev/90-nome-voice-uinput.rules` to:

```text
/etc/udev/rules.d/90-nome-voice-uinput.rules
```

Then load uinput:

```text
sudo modprobe uinput
```

Persist module loading with:

```text
/etc/modules-load.d/uinput.conf
```

containing:

```text
uinput
```

The installer should reload udev rules and tell the user if they need to log out and back in.

## Model Location

Default model:

```text
vosk-model-small-en-us-0.15
```

Install under:

```text
~/.local/share/nome-voice/models/vosk-model-small-en-us-0.15/
```

The GUI should let users choose another model path later.

## Automated Setup

The current repo-level setup script is:

```text
setup.sh
```

Run it from the repo root:

```bash
bash setup.sh
```

It installs missing CachyOS/Arch packages with pacman, applies uinput setup, downloads
the default Vosk model, builds/installs the Rust input daemon, installs the Python GUI
into the user site-packages, and creates the desktop launcher.
