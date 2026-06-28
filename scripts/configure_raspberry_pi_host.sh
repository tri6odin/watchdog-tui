#!/bin/sh
set -eu

# Prepare Raspberry Pi host for watchdog-tui:
# - install system packages used by checks and KMSCON
# - install bundled JetBrains Mono Compact font
# - configure KMSCON on tty1
# - write /usr/local/bin/kmscon-watchdog runner for the cloned repo
#
# The script expects the repo to be cloned first. It does not create
# watchdog.conf and does not start the TUI immediately.

APP_USER="${APP_USER:-pi}"
APP_DIR="${APP_DIR:-/home/$APP_USER/watchdog-tui}"
RUNNER_PATH="${RUNNER_PATH:-/usr/local/bin/kmscon-watchdog}"
KMSCON_CONF_DIR="${KMSCON_CONF_DIR:-/etc/kmscon}"
KMSCON_CONF_PATH="${KMSCON_CONF_PATH:-$KMSCON_CONF_DIR/kmscon.conf}"
KMSCON_OVERRIDE_DIR="${KMSCON_OVERRIDE_DIR:-/etc/systemd/system/kmsconvt@tty1.service.d}"
KMSCON_OVERRIDE_PATH="${KMSCON_OVERRIDE_PATH:-$KMSCON_OVERRIDE_DIR/override.conf}"
BOOKWORM_LIST_PATH="${BOOKWORM_LIST_PATH:-/etc/apt/sources.list.d/debian-bookworm.list}"
BOOKWORM_PREF_PATH="${BOOKWORM_PREF_PATH:-/etc/apt/preferences.d/99-kmscon-from-bookworm}"
FONT_DIR="${FONT_DIR:-/usr/local/share/fonts/JetBrainsMono}"
KMSCON_FONT_NAME="${KMSCON_FONT_NAME:-}"
KMSCON_FONT_SIZE="${KMSCON_FONT_SIZE:-18}"
KMSCON_FONT_DPI="${KMSCON_FONT_DPI:-96}"
REMOVE_BOOKWORM_AFTER_INSTALL="${REMOVE_BOOKWORM_AFTER_INSTALL:-1}"

SCRIPT_DIR=$(
    CDPATH= cd -- "$(dirname -- "$0")"
    pwd
)
REPO_DIR=$(
    CDPATH= cd -- "$SCRIPT_DIR/.."
    pwd
)
FONT_SOURCE="${FONT_SOURCE:-$REPO_DIR/docs/fonts/JetBrainsMono-Compact.ttf}"

log() {
    printf '%s\n' "$*" >&2
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log "Run this script as root: sudo $0"
        exit 1
    fi
}

require_user() {
    if ! id -u "$APP_USER" >/dev/null 2>&1; then
        log "User '$APP_USER' does not exist."
        exit 1
    fi
}

require_repo() {
    if [ ! -d "$APP_DIR" ]; then
        log "App dir does not exist: $APP_DIR"
        log "Clone the linux branch there before running this script."
        exit 1
    fi
}

install_base_packages() {
    log "Installing base packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y git python3 iputils-ping curl smbclient network-manager iw rfkill fontconfig
}

write_bookworm_repo() {
    log "Writing temporary APT source + pinning for kmscon from bookworm"
    install -d /etc/apt/sources.list.d /etc/apt/preferences.d

    cat >"$BOOKWORM_LIST_PATH" <<'EOF'
deb http://deb.debian.org/debian bookworm main
EOF

    cat >"$BOOKWORM_PREF_PATH" <<'EOF'
Package: *
Pin: release n=bookworm
Pin-Priority: 1

Package: kmscon libtsm4 libutempter0
Pin: release n=bookworm
Pin-Priority: 990
EOF
}

remove_bookworm_repo() {
    if [ "$REMOVE_BOOKWORM_AFTER_INSTALL" = "1" ]; then
        log "Removing temporary bookworm APT source"
        rm -f "$BOOKWORM_LIST_PATH" "$BOOKWORM_PREF_PATH"
        apt-get update
    fi
}

install_kmscon_packages() {
    log "Installing kmscon"
    export DEBIAN_FRONTEND=noninteractive
    if apt-get install -y kmscon libtsm4 libutempter0; then
        return 0
    fi

    write_bookworm_repo
    apt-get update
    apt-get install -y kmscon libtsm4 libutempter0
    remove_bookworm_repo
}

detect_font_name() {
    if [ -n "$KMSCON_FONT_NAME" ]; then
        printf '%s\n' "$KMSCON_FONT_NAME"
        return 0
    fi

    if [ -f "$FONT_SOURCE" ] && command -v fc-scan >/dev/null 2>&1; then
        detected=$(fc-scan --format '%{family[0]}\n' "$FONT_SOURCE" 2>/dev/null | head -n 1 | sed 's/[[:space:]]*$//')
        if [ -n "$detected" ]; then
            printf '%s\n' "$detected"
            return 0
        fi
    fi

    printf '%s\n' "JetBrains Mono Compact"
}

install_font() {
    if [ ! -f "$FONT_SOURCE" ]; then
        log "Font file not found at $FONT_SOURCE; skipping font install"
        return 0
    fi

    log "Installing font from $FONT_SOURCE"
    install -d "$FONT_DIR"
    install -m 0644 "$FONT_SOURCE" "$FONT_DIR/$(basename "$FONT_SOURCE")"
    fc-cache -fv >/dev/null
}

write_kmscon_conf() {
    font_name=$(detect_font_name)
    log "Writing $KMSCON_CONF_PATH (font-name=$font_name)"
    install -d "$KMSCON_CONF_DIR"

    cat >"$KMSCON_CONF_PATH" <<EOF
font-name=$font_name
font-size=$KMSCON_FONT_SIZE
font-dpi=$KMSCON_FONT_DPI
EOF
}

write_runner() {
    log "Writing runner script $RUNNER_PATH"
    cat >"$RUNNER_PATH" <<EOF
#!/bin/sh
set -eu

APP_DIR=$APP_DIR

cd "\$APP_DIR"
exec /usr/bin/python3 watchdog_tui.py --config "\$APP_DIR/watchdog.conf" --db "\$APP_DIR/watchdog.sqlite3"
EOF
    chmod 0755 "$RUNNER_PATH"
}

write_kmscon_override() {
    log "Writing systemd override for kmsconvt@tty1"
    install -d "$KMSCON_OVERRIDE_DIR"

    cat >"$KMSCON_OVERRIDE_PATH" <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/kmscon --configdir=$KMSCON_CONF_DIR --vt=tty1 --seats=seat0 --no-switchvt --login -- $RUNNER_PATH
Restart=always
RestartSec=1
EOF
}

configure_systemd() {
    log "Configuring systemd"
    systemctl daemon-reload
    systemctl disable getty@tty1.service >/dev/null 2>&1 || true
    systemctl mask getty@tty1.service >/dev/null 2>&1 || true
    systemctl disable lightdm.service >/dev/null 2>&1 || true
    systemctl set-default multi-user.target
    systemctl enable kmsconvt@tty1
}

print_summary() {
    cat >&2 <<EOF

Host configuration complete.

App dir:           $APP_DIR
Runner:            $RUNNER_PATH
Font:              $FONT_SOURCE
kmscon config:     $KMSCON_CONF_PATH
systemd override:  $KMSCON_OVERRIDE_PATH

Next steps:
  cd $APP_DIR
  cp watchdog.example.conf watchdog.conf
  nano watchdog.conf
  sudo python3 watchdog_tui.py --once
  sudo systemctl restart kmsconvt@tty1

Useful checks:
  systemctl cat kmsconvt@tty1
  systemctl status kmsconvt@tty1 --no-pager -l
  journalctl -u kmsconvt@tty1 -b --no-pager -n 120
EOF
}

main() {
    require_root
    require_user
    require_repo
    install_base_packages
    install_kmscon_packages
    install_font
    write_kmscon_conf
    write_runner
    write_kmscon_override
    configure_systemd
    print_summary
}

main "$@"
