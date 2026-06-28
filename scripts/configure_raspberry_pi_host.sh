#!/bin/sh
set -eu

# Prepare Raspberry Pi host for watchdog-tui:
# - install system packages used by checks and KMSCON
# - install bundled JetBrains Mono Compact font
# - configure KMSCON on tty1
# - write /usr/local/bin/kmscon-watchdog runner for the cloned repo
# - optionally create watchdog.conf interactively
#
# The script expects the repo to be cloned first. It does not start the TUI
# immediately, so watchdog.conf can be verified before KMSCON takes tty1.

APP_DIR="${APP_DIR:-}"
RUNNER_PATH="${RUNNER_PATH:-/usr/local/bin/kmscon-watchdog}"
KMSCON_CONF_DIR="${KMSCON_CONF_DIR:-/etc/kmscon}"
KMSCON_CONF_PATH="${KMSCON_CONF_PATH:-$KMSCON_CONF_DIR/kmscon.conf}"
KMSCON_OVERRIDE_DIR="${KMSCON_OVERRIDE_DIR:-/etc/systemd/system/kmsconvt@tty1.service.d}"
KMSCON_OVERRIDE_PATH="${KMSCON_OVERRIDE_PATH:-$KMSCON_OVERRIDE_DIR/override.conf}"
BOOKWORM_LIST_PATH="${BOOKWORM_LIST_PATH:-/etc/apt/sources.list.d/debian-bookworm.list}"
BOOKWORM_PREF_PATH="${BOOKWORM_PREF_PATH:-/etc/apt/preferences.d/99-kmscon-from-bookworm}"
FONT_DIR="${FONT_DIR:-/usr/local/share/fonts/JetBrainsMono}"
KMSCON_FONT_NAME="${KMSCON_FONT_NAME:-}"
KMSCON_FONT_SIZE="${KMSCON_FONT_SIZE:-16}"
KMSCON_FONT_DPI="${KMSCON_FONT_DPI:-96}"
REMOVE_BOOKWORM_AFTER_INSTALL="${REMOVE_BOOKWORM_AFTER_INSTALL:-1}"
CONFIGURE_WATCHDOG_CONF="${CONFIGURE_WATCHDOG_CONF:-1}"
OVERWRITE_WATCHDOG_CONF="${OVERWRITE_WATCHDOG_CONF:-0}"
ADVANCED_WATCHDOG_CONF="${ADVANCED_WATCHDOG_CONF:-0}"

SCRIPT_DIR=$(
    CDPATH= cd -- "$(dirname -- "$0")"
    pwd
)
REPO_DIR=$(
    CDPATH= cd -- "$SCRIPT_DIR/.."
    pwd
)
if [ -z "$APP_DIR" ]; then
    APP_DIR="$REPO_DIR"
fi
FONT_SOURCE="${FONT_SOURCE:-$REPO_DIR/docs/fonts/JetBrainsMono-Compact.ttf}"
WATCHDOG_CONFIG_PATH="${WATCHDOG_CONFIG_PATH:-$APP_DIR/watchdog.conf}"

log() {
    printf '%s\n' "$*" >&2
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log "Run this script as root: sudo $0"
        exit 1
    fi
}

require_repo() {
    if [ ! -d "$APP_DIR" ]; then
        log "App dir does not exist: $APP_DIR"
        log "Clone the linux branch there before running this script."
        exit 1
    fi
    if [ ! -f "$APP_DIR/watchdog_tui.py" ]; then
        log "watchdog_tui.py not found in $APP_DIR"
        log "Run this script from the cloned watchdog-tui repo or set APP_DIR."
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
WATCHDOG_CONFIG_PATH=$WATCHDOG_CONFIG_PATH

cd "\$APP_DIR"
exec /usr/bin/python3 watchdog_tui.py --config "\$WATCHDOG_CONFIG_PATH" --db "\$APP_DIR/watchdog.sqlite3"
EOF
    chmod 0755 "$RUNNER_PATH"
}

ask() {
    prompt=$1
    default=$2
    if [ -n "$default" ]; then
        printf '%s [%s]: ' "$prompt" "$default" >&2
    else
        printf '%s: ' "$prompt" >&2
    fi
    IFS= read -r value || value=
    if [ -z "$value" ]; then
        value=$default
    fi
    printf '%s\n' "$value"
}

ask_secret() {
    prompt=$1
    printf '%s: ' "$prompt" >&2
    if [ -t 0 ]; then
        old_stty=$(stty -g)
        trap 'stty "$old_stty"; exit 130' INT TERM HUP
        stty -echo
        IFS= read -r value || value=
        stty "$old_stty"
        trap - INT TERM HUP
        printf '\n' >&2
    else
        IFS= read -r value || value=
    fi
    printf '%s\n' "$value"
}

ask_required_secret() {
    prompt=$1
    while :; do
        value=$(ask_secret "$prompt")
        if [ -n "$value" ]; then
            printf '%s\n' "$value"
            return 0
        fi
        log "Value cannot be empty."
    done
}

write_watchdog_config() {
    if [ "$CONFIGURE_WATCHDOG_CONF" != "1" ]; then
        log "Skipping watchdog.conf generation"
        return 0
    fi

    if [ -f "$WATCHDOG_CONFIG_PATH" ] && [ "$OVERWRITE_WATCHDOG_CONF" != "1" ]; then
        log "Config already exists: $WATCHDOG_CONFIG_PATH"
        log "Keeping it. Set OVERWRITE_WATCHDOG_CONF=1 to regenerate."
        return 0
    fi

    if [ ! -t 0 ]; then
        log "Interactive terminal is required to create $WATCHDOG_CONFIG_PATH."
        log "Create watchdog.conf manually or rerun with CONFIGURE_WATCHDOG_CONF=0."
        exit 1
    fi

    log "Creating watchdog config at $WATCHDOG_CONFIG_PATH"

    lcl_net_address=$(ask "Local gateway address" "192.168.50.1")
    int_net_host=$(ask "Internet TLS host" "ya.ru")
    vpn_host=$(ask "VPN TLS host" "instagram.com")

    haos_address=$(ask "Home Assistant address" "192.168.50.4")
    haos_token=$(ask_required_secret "Home Assistant long-lived token")

    nas_address=$(ask "NAS address" "192.168.50.3")
    nas_user=$(ask "NAS SMB user" "watchdog")
    nas_pass=$(ask_required_secret "NAS SMB password")
    nas_share=$(ask "NAS SMB share" "BACKUP")

    wifi_ssid=$(ask "Main Wi-Fi network name (SSID)" "example-wifi")
    wifi_pass=$(ask_required_secret "Main Wi-Fi password")

    iot_wifi_ssid=$(ask "IoT Wi-Fi network name (SSID)" "example-iot")
    iot_wifi_pass=$(ask_secret "IoT Wi-Fi password (empty = same as main Wi-Fi)")
    if [ -z "$iot_wifi_pass" ]; then
        iot_wifi_pass=$wifi_pass
    fi

    global_http_user_agent=watchdog-tui/1.0
    lcl_net_timeout=1.0
    int_net_port=443
    int_net_timeout=1.0
    vpn_port=443
    vpn_timeout=5.0
    haos_port=8123
    haos_api_path=/api/config
    haos_timeout=2.0
    nas_port=445
    nas_tcp_timeout=1.0
    nas_smb_timeout=5.0
    wifi_gateway=$lcl_net_address
    wifi_interface=auto
    wifi_connect_timeout=25.0
    wifi_ping_timeout=1.0
    wifi_disconnect_after=true
    iot_wifi_gateway=$lcl_net_address
    iot_wifi_interface=auto
    iot_wifi_connect_timeout=25.0
    iot_wifi_ping_timeout=1.0
    iot_wifi_disconnect_after=true

    touch_enabled=true
    touch_event_device=auto
    touch_swipe_min_dx=1200
    touch_swipe_max_dt=2.2
    touch_swipe_ratio=1.1

    if [ "$ADVANCED_WATCHDOG_CONF" = "1" ]; then
        log "Advanced config prompts enabled"
        global_http_user_agent=$(ask "HTTP user agent" "$global_http_user_agent")
        lcl_net_timeout=$(ask "Local ping timeout seconds" "$lcl_net_timeout")
        int_net_port=$(ask "Internet TLS port" "$int_net_port")
        int_net_timeout=$(ask "Internet TLS timeout seconds" "$int_net_timeout")
        vpn_port=$(ask "VPN TLS port" "$vpn_port")
        vpn_timeout=$(ask "VPN TLS timeout seconds" "$vpn_timeout")
        haos_port=$(ask "Home Assistant port" "$haos_port")
        haos_api_path=$(ask "Home Assistant API path" "$haos_api_path")
        haos_timeout=$(ask "Home Assistant timeout seconds" "$haos_timeout")
        nas_port=$(ask "NAS SMB port" "$nas_port")
        nas_tcp_timeout=$(ask "NAS TCP timeout seconds" "$nas_tcp_timeout")
        nas_smb_timeout=$(ask "NAS SMB timeout seconds" "$nas_smb_timeout")
        wifi_gateway=$(ask "Main Wi-Fi gateway" "$wifi_gateway")
        wifi_interface=$(ask "Main Wi-Fi interface" "$wifi_interface")
        wifi_connect_timeout=$(ask "Main Wi-Fi connect timeout seconds" "$wifi_connect_timeout")
        wifi_ping_timeout=$(ask "Main Wi-Fi ping timeout seconds" "$wifi_ping_timeout")
        wifi_disconnect_after=$(ask "Main Wi-Fi disconnect after check" "$wifi_disconnect_after")
        iot_wifi_gateway=$(ask "IoT Wi-Fi gateway" "$iot_wifi_gateway")
        iot_wifi_interface=$(ask "IoT Wi-Fi interface" "$iot_wifi_interface")
        iot_wifi_connect_timeout=$(ask "IoT Wi-Fi connect timeout seconds" "$iot_wifi_connect_timeout")
        iot_wifi_ping_timeout=$(ask "IoT Wi-Fi ping timeout seconds" "$iot_wifi_ping_timeout")
        iot_wifi_disconnect_after=$(ask "IoT Wi-Fi disconnect after check" "$iot_wifi_disconnect_after")
    fi

    old_umask=$(umask)
    umask 077
    cat >"$WATCHDOG_CONFIG_PATH" <<EOF
[global]
http_user_agent = $global_http_user_agent

[lcl_net]
address = $lcl_net_address
timeout_seconds = $lcl_net_timeout

[int_net]
host = $int_net_host
port = $int_net_port
timeout_seconds = $int_net_timeout

[vpn]
host = $vpn_host
port = $vpn_port
timeout_seconds = $vpn_timeout

[haos]
address = $haos_address
port = $haos_port
api_path = $haos_api_path
timeout_seconds = $haos_timeout
token = $haos_token

[nas]
address = $nas_address
port = $nas_port
tcp_timeout_seconds = $nas_tcp_timeout
smb_timeout_seconds = $nas_smb_timeout
smb_user = $nas_user
smb_pass = $nas_pass
smb_share = $nas_share

[wifi]
ssid = $wifi_ssid
password = $wifi_pass
gateway = $wifi_gateway
interface = $wifi_interface
connect_timeout_seconds = $wifi_connect_timeout
ping_timeout_seconds = $wifi_ping_timeout
disconnect_after_check = $wifi_disconnect_after

[iot_wifi]
ssid = $iot_wifi_ssid
password = $iot_wifi_pass
gateway = $iot_wifi_gateway
interface = $iot_wifi_interface
connect_timeout_seconds = $iot_wifi_connect_timeout
ping_timeout_seconds = $iot_wifi_ping_timeout
disconnect_after_check = $iot_wifi_disconnect_after

[touch]
enabled = $touch_enabled
event_device = $touch_event_device
swipe_min_dx = $touch_swipe_min_dx
swipe_max_dt_sec = $touch_swipe_max_dt
swipe_horizontal_ratio = $touch_swipe_ratio
EOF
    chmod 0600 "$WATCHDOG_CONFIG_PATH"
    umask "$old_umask"
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
Config:            $WATCHDOG_CONFIG_PATH
Runner:            $RUNNER_PATH
Font:              $FONT_SOURCE
kmscon config:     $KMSCON_CONF_PATH
systemd override:  $KMSCON_OVERRIDE_PATH

Next steps:
  cd $APP_DIR
  sudo python3 watchdog_tui.py --config "$WATCHDOG_CONFIG_PATH" --once
  sudo systemctl restart kmsconvt@tty1

Useful checks:
  systemctl cat kmsconvt@tty1
  systemctl status kmsconvt@tty1 --no-pager -l
  journalctl -u kmsconvt@tty1 -b --no-pager -n 120
EOF
}

main() {
    require_root
    require_repo
    install_base_packages
    install_kmscon_packages
    install_font
    write_kmscon_conf
    write_watchdog_config
    write_runner
    write_kmscon_override
    configure_systemd
    print_summary
}

main "$@"
