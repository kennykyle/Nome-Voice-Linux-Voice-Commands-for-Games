#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Nome Voice"
APP_ID="local.nomevoice.NomeVoice"
APP_ICON_NAME="nome-voice"
DESKTOP_FILE_NAME="${APP_ID}.desktop"
DEFAULT_MODEL_NAME="vosk-model-small-en-us-0.15"
DEFAULT_MODEL_URL="https://alphacephei.com/vosk/models/${DEFAULT_MODEL_NAME}.zip"

SKIP_UINPUT=0
SKIP_MODEL=0
SKIP_GUI=0
WITH_RUST=1
PAUSE_AT_END=1

SYSTEM_PACKAGES=(
  acl
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
  bzip2
  tar
  unzip
)

section() {
  printf '\n\033[1;36m%s\033[0m\n' "========================================"
  printf '\033[1;36m%s\033[0m\n\n' "$*"
}

ok() {
  printf '  \033[1;32m[OK]\033[0m %s\n' "$*"
}

warn() {
  printf '  \033[1;33m[!]\033[0m %s\n' "$*" >&2
}

info() {
  printf '  \033[1;34m[>]\033[0m %s\n' "$*"
}

die() {
  printf '\n\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2
  exit 1
}

repo_root() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  cd -- "${script_dir}/.." && pwd
}

has_command() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<EOF
Usage:
  bash setup.sh [options]
  bash scripts/setup-guide.sh [options]

Automated Nome Voice setup for CachyOS/Arch GNOME Wayland.

Options:
  --skip-uinput   Do not apply uinput sudo setup.
  --skip-model    Do not download the default Vosk model.
  --skip-gui      Do not install or update the nome-voice command.
  --skip-rust     Do not build, install, or start the Rust input daemon.
  --with-rust     Build the Rust input daemon. This is now the default.
  --no-pause      Do not wait for Enter at the end.
  -h, --help      Show this help.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-uinput)
        SKIP_UINPUT=1
        shift
        ;;
      --skip-model)
        SKIP_MODEL=1
        shift
        ;;
      --skip-gui)
        SKIP_GUI=1
        shift
        ;;
      --skip-rust)
        WITH_RUST=0
        shift
        ;;
      --with-rust)
        WITH_RUST=1
        shift
        ;;
      --no-pause)
        PAUSE_AT_END=0
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

show_banner() {
  clear || true
  printf '\033[1;36m%s\033[0m\n' "Nome Voice"
  printf '\n%s\n' "Automated setup for CachyOS / Arch GNOME Wayland"
}

check_environment() {
  section "1. Installing System Dependencies"

  if [[ "${EUID}" -eq 0 ]]; then
    die "Run setup as your normal user, not root. The script asks sudo only when needed."
  fi

  local missing=()
  local required=(sudo pacman)
  local missing_packages=()

  for command_name in "${required[@]}"; do
    if has_command "${command_name}"; then
      ok "${command_name} found"
    else
      warn "${command_name} not found"
      missing+=("${command_name}")
    fi
  done

  if [[ "${#missing[@]}" -gt 0 ]]; then
    die "Missing required tools: ${missing[*]}. This setup currently supports CachyOS/Arch with pacman."
  fi

  for package_name in "${SYSTEM_PACKAGES[@]}"; do
    if pacman -Q "${package_name}" >/dev/null 2>&1; then
      ok "${package_name} already installed"
    else
      warn "${package_name} missing"
      missing_packages+=("${package_name}")
    fi
  done

  if [[ "${#missing_packages[@]}" -gt 0 ]]; then
    info "Installing missing packages: ${missing_packages[*]}"
    sudo pacman -S --needed --noconfirm "${missing_packages[@]}"
  else
    ok "All system packages are already installed"
  fi

  section "2. Checking Runtime Tools"

  missing=()
  required=(python curl tar unzip)

  if [[ "${SKIP_UINPUT}" -eq 0 ]]; then
    required+=(sudo)
  fi

  if [[ "${WITH_RUST}" -eq 1 ]]; then
    required+=(cargo rustc)
  fi

  for command_name in "${required[@]}"; do
    if has_command "${command_name}"; then
      ok "${command_name} found"
    else
      warn "${command_name} not found"
      missing+=("${command_name}")
    fi
  done

  if [[ "${#missing[@]}" -gt 0 ]]; then
    die "Missing required tools: ${missing[*]}. Install them using your normal CachyOS tools, then run this again."
  fi

  if ! python -m pip --version >/dev/null 2>&1; then
    die "Python pip is not available. Install python-pip using your normal CachyOS tools, then run this again."
  fi

  ok "python pip found"

  local desktop_name="${XDG_CURRENT_DESKTOP:-unknown}"
  local session_type="${XDG_SESSION_TYPE:-unknown}"
  if [[ "${desktop_name,,}" == *gnome* ]]; then
    ok "GNOME desktop detected (${desktop_name})"
  else
    warn "GNOME was not detected (${desktop_name}). Nome Voice currently supports GNOME only."
  fi

  if [[ "${session_type,,}" == "wayland" ]]; then
    ok "Wayland session detected"
  else
    warn "Wayland was not detected (${session_type}). CachyOS/Arch GNOME Wayland is the target setup."
  fi
}

setup_uinput() {
  section "3. Setting Up uinput"

  if [[ "${SKIP_UINPUT}" -eq 1 ]]; then
    info "Skipped uinput setup"
    return
  fi

  local rule_src="${ROOT}/packaging/udev/90-nome-voice-uinput.rules"
  local rule_dst="/etc/udev/rules.d/90-nome-voice-uinput.rules"

  [[ -f "${rule_src}" ]] || die "Missing udev rule: ${rule_src}"

  info "Requesting sudo access for uinput setup"
  sudo -v

  local target_user
  target_user="${SUDO_USER:-${USER}}"

  if getent group uinput >/dev/null 2>&1; then
    ok "uinput group exists"
  else
    info "Creating uinput group"
    sudo groupadd --system uinput
  fi

  if id -nG "${target_user}" | tr ' ' '\n' | grep -qx uinput; then
    ok "${target_user} is already in uinput group"
  else
    info "Adding ${target_user} to uinput group"
    sudo gpasswd -a "${target_user}" uinput >/dev/null
  fi

  info "Installing udev rule"
  sudo install -Dm644 "${rule_src}" "${rule_dst}"

  info "Enabling uinput module at boot"
  printf 'uinput\n' | sudo tee /etc/modules-load.d/uinput.conf >/dev/null

  info "Loading uinput now"
  sudo modprobe uinput

  info "Reloading udev rules"
  sudo udevadm control --reload-rules
  sudo udevadm trigger || true

  if [[ -e /dev/uinput ]]; then
    info "Applying uinput permissions for this boot"
    sudo chgrp uinput /dev/uinput || true
    sudo chmod 660 /dev/uinput || true
    if command -v setfacl >/dev/null 2>&1; then
      sudo setfacl -m "u:${target_user}:rw" /dev/uinput || true
    else
      warn "setfacl not found; group access may require logging out and back in."
    fi
  fi

  if [[ -e /dev/uinput ]]; then
    ok "/dev/uinput exists"
  else
    warn "/dev/uinput does not exist yet. Reboot may be required."
  fi

  if [[ -r /dev/uinput && -w /dev/uinput ]]; then
    ok "Current user can access /dev/uinput"
  else
    warn "Current shell may not see the new uinput group yet. Log out and back in if access still fails."
  fi
}

download_default_model() {
  section "4. Preparing Speech Models"

  if [[ "${SKIP_MODEL}" -eq 1 ]]; then
    info "Skipped model downloads"
    return
  fi

  local data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
  local model_parent="${data_home}/nome-voice/models"
  mkdir -p "${model_parent}"

  download_vosk_model "${model_parent}"
}

download_vosk_model() {
  local model_parent="$1"
  local model_dir="${model_parent}/${DEFAULT_MODEL_NAME}"
  local zip_path="${model_parent}/${DEFAULT_MODEL_NAME}.zip"

  if [[ -d "${model_dir}" ]]; then
    ok "Default Vosk model already exists"
    info "${model_dir}"
    return
  fi

  rm -f "${zip_path}"

  info "Downloading ${DEFAULT_MODEL_NAME}"
  curl -L --fail --progress-bar --output "${zip_path}" "${DEFAULT_MODEL_URL}"

  info "Extracting model"
  unzip -q "${zip_path}" -d "${model_parent}"
  rm -f "${zip_path}"

  [[ -d "${model_dir}" ]] || die "Model extraction did not create ${model_dir}"
  ok "Vosk model ready at ${model_dir}"
}

cleanup_legacy_daemon() {
  local user_systemd_dir="$1"
  local local_bin="$2"
  local legacy_services=(
    voxactiond.service
    vox-actiond.service
    voxactionsd.service
    voxaction.service
    vox-action.service
  )
  local legacy_bins=(
    voxactiond
    vox-actiond
    voxactionsd
    voxaction
    vox-action
  )

  if command -v systemctl >/dev/null 2>&1; then
    for service in "${legacy_services[@]}"; do
      systemctl --user disable --now "${service}" >/dev/null 2>&1 || true
      rm -f "${user_systemd_dir}/${service}"
    done
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  fi

  if command -v pkill >/dev/null 2>&1; then
    for process_name in "${legacy_bins[@]}"; do
      pkill -u "$(id -u)" -x "${process_name}" >/dev/null 2>&1 || true
    done
  fi

  for binary_name in "${legacy_bins[@]}"; do
    rm -f "${local_bin}/${binary_name}"
  done
}

build_project() {
  section "5. Installing Rust Input Daemon"

  local local_bin="${HOME}/.local/bin"
  local user_systemd_dir="${HOME}/.config/systemd/user"

  mkdir -p "${local_bin}"
  mkdir -p "${user_systemd_dir}"
  cleanup_legacy_daemon "${user_systemd_dir}" "${local_bin}"

  if [[ "${WITH_RUST}" -eq 0 ]]; then
    info "Skipped Rust daemon install"
    return
  fi

  local service_path="${user_systemd_dir}/nome-voiced.service"
  local daemon_bin="${local_bin}/nome-voiced"

  info "Building nome-voiced release binary"
  cargo build --release -p nome-voiced

  info "Installing nome-voiced to ${daemon_bin}"
  install -Dm755 "${ROOT}/target/release/nome-voiced" "${daemon_bin}"

  cat >"${service_path}" <<EOF
[Unit]
Description=Nome Voice input daemon

[Service]
ExecStart=${daemon_bin}
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
EOF

  if command -v systemctl >/dev/null 2>&1; then
    info "Enabling and starting nome-voiced user service"
    systemctl --user daemon-reload || true
    systemctl --user enable --now nome-voiced.service || warn "Could not start nome-voiced user service automatically"
  else
    warn "systemctl not found; start nome-voiced manually with ${daemon_bin}"
  fi

  ok "Rust daemon installed"
}

install_gui_entrypoint() {
  section "6. Installing Nome Voice App"

  if [[ "${SKIP_GUI}" -eq 1 ]]; then
    info "Skipped GUI command install"
    return
  fi

  info "Installing or updating nome-voice for current user"
  local pip_args=(
    install
    --user
    --upgrade
    --upgrade-strategy
    only-if-needed
    --no-input
  )
  if ! python -m pip "${pip_args[@]}" --break-system-packages .; then
    warn "Retrying without --break-system-packages"
    python -m pip "${pip_args[@]}" .
  fi

  local user_base
  local gui_bin
  user_base="$(python -m site --user-base)"
  gui_bin="${user_base}/bin/nome-voice"
  [[ -x "${gui_bin}" ]] || die "nome-voice was not installed at ${gui_bin}"

  ok "nome-voice command installed at ${gui_bin}"
}

install_desktop_entry() {
  section "7. Installing Desktop Launcher"

  if [[ "${SKIP_GUI}" -eq 1 ]]; then
    info "Skipped desktop launcher because GUI install was skipped"
    return
  fi

  local desktop_dir="${HOME}/.local/share/applications"
  local desktop_path="${desktop_dir}/${DESKTOP_FILE_NAME}"
  local icon_source="${ROOT}/packaging/icons/hicolor/scalable/apps/${APP_ICON_NAME}.svg"
  local icon_path="${HOME}/.local/share/icons/hicolor/scalable/apps/${APP_ICON_NAME}.svg"
  local local_bin="${HOME}/.local/bin"
  local user_base
  local gui_bin
  local launcher_path
  local log_dir
  user_base="$(python -m site --user-base)"
  gui_bin="${user_base}/bin/nome-voice"
  launcher_path="${local_bin}/nome-voice-launcher"
  log_dir="${XDG_CACHE_HOME:-${HOME}/.cache}/nome-voice"

  mkdir -p "${desktop_dir}"
  mkdir -p "${local_bin}"
  mkdir -p "${log_dir}"
  install -Dm644 "${icon_source}" "${icon_path}"

  for legacy_entry in \
    "${desktop_dir}/nome-voice.desktop" \
    "${desktop_dir}/vox-action.desktop" \
    "${desktop_dir}/vox-actions.desktop" \
    "${desktop_dir}/voxaction.desktop"; do
    if [[ -f "${legacy_entry}" && "${legacy_entry}" != "${desktop_path}" ]]; then
      rm -f "${legacy_entry}"
    fi
  done

cat >"${launcher_path}" <<EOF
#!/usr/bin/env bash
mkdir -p "${log_dir}"
echo "----- Nome Voice launch \$(date) -----" >> "${log_dir}/nome-voice.log"
if [[ -x "${gui_bin}" ]]; then
  exec "${gui_bin}" >> "${log_dir}/nome-voice.log" 2>&1
fi
exec python -m nome_voice.main >> "${log_dir}/nome-voice.log" 2>&1
EOF
  chmod +x "${launcher_path}"

  cat >"${desktop_path}" <<EOF
[Desktop Entry]
Type=Application
Name=Nome Voice
Comment=Voice commands for games
Exec=${launcher_path}
Icon=${APP_ICON_NAME}
Terminal=false
Categories=Utility;Accessibility;
StartupWMClass=${APP_ID}
EOF

  gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${desktop_dir}" >/dev/null 2>&1 || true
  fi

  ok "Desktop launcher installed"
  info "GUI launch log: ${log_dir}/nome-voice.log"
}

final_screen() {
  section "Done"

  cat <<EOF
${APP_NAME} automated setup finished.

Start Nome Voice:

  nome-voice

Or click Nome Voice in the GNOME app list.

Desktop launch logs:

  ${XDG_CACHE_HOME:-${HOME}/.cache}/nome-voice/nome-voice.log

If uinput access fails, log out and back in, or reboot once.

Daemon status:

  systemctl --user status nome-voiced.service
EOF

  if [[ "${PAUSE_AT_END}" -eq 1 && -t 0 ]]; then
    printf '\nPress Enter to close this setup window... '
    read -r _
  fi
}

ROOT="$(repo_root)"
cd "${ROOT}"

parse_args "$@"
show_banner
check_environment
setup_uinput
download_default_model
build_project
install_gui_entrypoint
install_desktop_entry
final_screen
