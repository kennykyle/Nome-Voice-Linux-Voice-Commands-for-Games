# Implementation Checklist

## Ready

- [x] V1 scope chosen.
- [x] Middle-ground Rust daemon + Python GTK GUI path chosen.
- [x] uinput selected as the V1 input backend.
- [x] Vosk restricted grammar selected as the V1 speech backend.
- [x] JSON profile contract prepared.
- [x] Local IPC approach chosen.

## Build Next

- [x] Add Rust workspace skeleton.
- [x] Add Python GUI package skeleton.
- [x] Implement Rust profile structs and JSON loading.
- [x] Implement phrase normalization and duplicate detection.
- [x] Implement command matcher tests.
- [x] Implement daemon Unix socket dry-run API.
- [x] Implement Python GUI IPC client scaffold.
- [x] Implement MVP GTK GUI.
- [x] Implement uinput diagnostic check.
- [x] Implement uinput action runner.
- [x] Implement Vosk recognizer worker.
- [x] Implement import/export.
- [x] Implement first-run setup prompt.
- [x] Implement CachyOS/Arch automated setup script.

## Test Next

- [ ] Validate sample profile against Rust profile loader with `cargo test`.
- [ ] Unit test phrase matching with `cargo test`.
- [ ] Unit test action sequence planning without real uinput.
- [ ] Manual test daemon IPC on Linux.
- [ ] Manual test MVP GUI startup.
- [ ] Manual test uinput key press on GNOME Wayland.
- [ ] Manual test recognition latency with small Vosk model.
- [ ] Manual test fullscreen game or Proton game.
