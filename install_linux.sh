#!/usr/bin/env bash
# Install Lifetime for the current user (no root). Two layouts:
#  - the self-contained bundle (Lifetime-Linux-ARCH.tar.gz: no system Python or PyQt6 needed, any glibc 2.28+ desktop)
#    goes to ~/.local/lib/lifetime;
#  - a source checkout (lifetime_*.py) goes to ~/.local/share/lifetime and runs on the system's Python 3.11+ and PyQt6.
# Tracking at login is chosen later in Lifetime: a systemd user service where systemd runs, XDG autostart elsewhere.
set -euo pipefail
company_agent=false
case "${1:-}" in
    '') ;;
    --company-agent) company_agent=true ;;
    *) printf 'Usage: install_linux.sh [--company-agent]\n'; exit 2 ;;
esac
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"

# Escape paths for systemd's quoted argument syntax and percent specifiers.
unit_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    printf '"%s"' "$value"
}
# Desktop Exec values use a separate quoting grammar.
desktop_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\\\$}"
    value="${value//\`/\\\`}"
    value="${value//%/%%}"
    printf '"%s"' "$value"
}
systemd_user() {
    [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1
}
# write_unit FILE DESCRIPTION EXTRA-LINES COMMAND...
write_unit() {
    local file="$1" description="$2" extra="$3" first=true
    shift 3
    mkdir -p "$(dirname "$file")"
    {
        printf '[Unit]\nDescription=%s\n\n[Service]\nType=simple\nExecStart=' "$description"
        for argument in "$@"; do
            [[ "$first" == true ]] || printf ' '
            first=false
            unit_quote "$argument"
        done
        printf '\nRestart=on-failure\nRestartSec=5\n%s\n[Install]\nWantedBy=default.target\n' "$extra"
    } > "$file"
}
# write_autostart FILE NAME COMMAND...: XDG autostart, honoured by GNOME, KDE, XFCE, Cinnamon, MATE, LXQt and Budgie
write_autostart() {
    local file="$1" name="$2" first=true
    shift 2
    mkdir -p "$(dirname "$file")"
    {
        printf '[Desktop Entry]\nType=Application\nName=%s\nExec=' "$name"
        for argument in "$@"; do
            [[ "$first" == true ]] || printf ' '
            first=false
            desktop_quote "$argument"
        done
        printf '\nTerminal=false\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n'
    } > "$file"
}
journal_access() {
    # Journal access is optional. Never invoke sudo without an affirmative answer.
    command -v journalctl >/dev/null 2>&1 || {
        printf 'This system has no systemd journal: Lifetime counts from now on and cannot add earlier usage.\n'
        return 0
    }
    local journal_test answer
    journal_test="$(LC_ALL=C journalctl --system -n 1 --no-pager 2>&1)" || journal_test="permission denied"
    if [[ "$journal_test" == *"not seeing messages"* || "$journal_test" == *"permission denied"* || "$journal_test" == *"insufficient permissions"* ]]; then
        printf 'Reading the full system journal lets Lifetime add the time this computer was used before you installed it. Adding your user to the systemd-journal group needs your password and takes effect after you sign out and back in.\n'
        read -r -p 'Run sudo usermod -aG systemd-journal for your user? (y/N) ' answer || answer=''
        if [[ "$answer" == 'y' || "$answer" == 'Y' ]]; then
            sudo usermod -aG systemd-journal "$(id -un)" || printf 'Journal access unchanged; earlier usage may be incomplete.\n'
        else
            printf 'Journal access not changed; earlier usage may be incomplete.\n'
        fi
    fi
}
# company_agent_service COMMAND...: the company reporting agent at login, after explicit consent
company_agent_service() {
    local answer agent_dir
    printf 'The workplace reporting service keeps its own awake-time record while you are signed in. Nothing is sent until you connect it on the Connect page with a code from your IT team and the server approves it. Optional history and system readings stay off.\n'
    read -r -p 'Install the workplace reporting service and start it automatically with the computer? (y/N) ' answer || answer=''
    [[ "$answer" == 'y' || "$answer" == 'Y' ]] || return 0
    agent_dir="${XDG_STATE_HOME:-$HOME/.local/state}/lifetime-agent"
    if [[ ! -f "$agent_dir/machine.sqlite" ]]; then
        "$@" --directory "$agent_dir" --initialize
    fi
    if systemd_user; then
        write_unit "$config_home/systemd/user/lifetime-company-agent.service" 'Lifetime company reporting agent' \
            $'UMask=0077\nNoNewPrivileges=true' "$@" --directory "$agent_dir" --run
        systemctl --user daemon-reload
        systemctl --user enable --now lifetime-company-agent.service
    else
        write_autostart "$config_home/autostart/lifetime-company-agent.desktop" 'Lifetime company agent' \
            "$@" --directory "$agent_dir" --run
        (umask 077; nohup "$@" --directory "$agent_dir" --run >/dev/null 2>&1 &)
        printf 'The workplace reporting service is running and now starts automatically each time you sign in.\n'
    fi
}

if [[ -x "$source_dir/Lifetime" && -d "$source_dir/_internal" ]]; then
    # ---- self-contained bundle ----
    glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')" || glibc=''
    if [[ -z "$glibc" ]]; then
        printf 'This system does not use glibc (for example Alpine/musl), which Lifetime for Linux needs.\n'; exit 1
    fi
    if [[ "$(printf '%s\n2.28\n' "$glibc" | sort -V | head -n 1)" != 2.28 ]]; then
        printf 'glibc %s is older than 2.28; Lifetime needs Debian 10, Ubuntu 20.04, RHEL 8 or newer.\n' "$glibc"; exit 1
    fi
    install_dir="$HOME/.local/lib/lifetime"
    rm -rf "$install_dir.new"
    mkdir -p "$install_dir.new"
    cp -a "$source_dir/." "$install_dir.new/"
    rm -rf "$install_dir.previous"
    [[ ! -d "$install_dir" ]] || mv "$install_dir" "$install_dir.previous"
    mv "$install_dir.new" "$install_dir"
    rm -rf "$install_dir.previous"
    mkdir -p "$HOME/.local/bin"
    ln -sfn "$install_dir/Lifetime" "$HOME/.local/bin/lifetime"
    # Upgrade from a source install: its program files sat in the data folder. Remove them (never the history), so
    # nothing can start the old version beside this one.
    rm -f "$HOME"/.local/share/lifetime/lifetime_*.py
    rm -rf "$HOME/.local/share/lifetime/assets" "$HOME/.local/share/lifetime/__pycache__"
    tracker=("$install_dir/LifetimeTracker" --run)
    agent=("$install_dir/LifetimeAgent")
    window=("$install_dir/Lifetime")
    icon="$install_dir/assets/lifetime.png"
else
    # ---- source checkout ----
    install_dir="$HOME/.local/share/lifetime"     # also the tracker's data folder, as before
    python_bin="$(command -v python3 || true)"
    if [[ -z "$python_bin" ]]; then
        printf 'Python 3 is required. Install it with your package manager (for example: sudo apt install python3), then run this installer again.\n'
        exit 1
    fi
    if ! "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        printf 'Lifetime needs Python 3.11 or newer; %s is older. Use the Lifetime-Linux bundle instead: it brings its own Python.\n' "$python_bin"
        exit 1
    fi
    if ! "$python_bin" -c 'import PyQt6.QtWidgets' >/dev/null 2>&1; then
        printf 'Lifetime needs PyQt6 for its window.\n'
        printf 'Distribution packages: Debian/Ubuntu: sudo apt install python3-pyqt6 | Fedora: sudo dnf install python3-pyqt6 | Arch: sudo pacman -S python-pyqt6\n'
        read -r -p 'Install PyQt6 for your user now with pip instead? (y/N) ' answer || answer=''
        if [[ "$answer" == 'y' || "$answer" == 'Y' ]]; then
            "$python_bin" -m pip install --user PyQt6 || "$python_bin" -m pip install --user --break-system-packages PyQt6
        else
            printf 'Install PyQt6 with one of the commands above, then run this installer again.\n'
            exit 1
        fi
    fi
    mkdir -p "$install_dir/assets"
    for file in lifetime_system.py lifetime_system_gui.py lifetime_tracker.py lifetime_gui.py lifetime_health.py lifetime_health_gui.py lifetime_shell.py lifetime_signals.py lifetime_dashboard.py lifetime_charts.py lifetime_settings.py lifetime_setup.py lifetime_import.py lifetime_import_windows.py lifetime_daily.py lifetime_downtime.py lifetime_uptime_gui.py lifetime_i18n.py lifetime_i18n_qt.py lifetime_reports.py lifetime_technician.py lifetime_ledger.py lifetime_identity.py lifetime_theme.py lifetime_tasks.py lifetime_company.py lifetime_connect.py lifetime_agent.py lifetime_live.py lifetime_reveal.py lifetime_glass.py lifetime_widgets_dashboard.py lifetime_insights.py lifetime_insights_gui.py lifetime_ws.py lifetime_ws_client.py lifetime_client.py lifetime_protocol.py lifetime_tls.py lifetime_permissions.py lifetime_connection.py lifetime_discovery.py lifetime_updates.py; do
        if [[ "$source_dir/$file" != "$install_dir/$file" ]]; then
            install -m 644 "$source_dir/$file" "$install_dir/$file"
        fi
    done
    for asset in lifetime.png jpl_innovation.png jpl_innovation_light.png; do
        install -m 644 "$source_dir/assets/$asset" "$install_dir/assets/$asset"
    done
    tracker=("$python_bin" "$install_dir/lifetime_tracker.py" --run)
    agent=("$python_bin" "$install_dir/lifetime_agent.py")
    window=("$python_bin" "$install_dir/lifetime_gui.py")
    icon="$install_dir/assets/lifetime.png"
fi

journal_access
# The tracker's login start is prepared here but switched on only when you choose it in Lifetime.
# Login entries written by an earlier version (by this installer or by Lifetime itself) must start this version's
# programs: an upgrade from a source install to the bundle moves them.
agent_dir="${XDG_STATE_HOME:-$HOME/.local/state}/lifetime-agent"
agent_unit="$config_home/systemd/user/lifetime-company-agent.service"
agent_entry="$config_home/autostart/lifetime-company-agent.desktop"
tracker_entry="$config_home/autostart/lifetime-tracker.desktop"
[[ ! -f "$tracker_entry" ]] || write_autostart "$tracker_entry" 'Lifetime tracker' "${tracker[@]}"
[[ ! -f "$agent_entry" ]] || write_autostart "$agent_entry" 'Lifetime company agent' "${agent[@]}" --directory "$agent_dir" --run
[[ ! -f "$agent_unit" ]] || write_unit "$agent_unit" 'Lifetime company reporting agent' \
    $'UMask=0077\nNoNewPrivileges=true' "${agent[@]}" --directory "$agent_dir" --run
if systemd_user; then
    write_unit "$config_home/systemd/user/lifetime-tracker.service" 'Lifetime awake-time tracker' '' "${tracker[@]}"
    systemctl --user daemon-reload
    # Running services continue with the new programs (try-restart leaves a stopped service stopped).
    systemctl --user try-restart lifetime-tracker.service lifetime-company-agent.service >/dev/null 2>&1 || true
fi
if [[ "$company_agent" == true ]]; then
    company_agent_service "${agent[@]}"
fi
mkdir -p "$data_home/applications"
{
    printf '[Desktop Entry]\nType=Application\nName=Lifetime\nComment=Total computer awake time\nExec='
    first=true
    for argument in "${window[@]}"; do
        [[ "$first" == true ]] || printf ' '
        first=false
        desktop_quote "$argument"
    done
    printf '\nIcon=%s\nTerminal=false\nCategories=Utility;\n' "$icon"
} > "$data_home/applications/lifetime.desktop"
if [[ -x "$install_dir/LifetimeServerDashboard" ]]; then
    # Gives the dashboard's app id (lifetime-server) its yellow icon in the dock and taskbar on Wayland. It is in the
    # app menu when Lifetime Server is installed on this computer (its program: its settings stay after an uninstall)
    # and this person may use it, as a member of lifetime-admin or lifetime-console (read from the group database, so
    # a membership given before their next sign-in counts). install_server_linux.sh shows it when the server comes second.
    hidden='NoDisplay=true\n'
    if [[ -x /opt/lifetime-server/LifetimeServerRuntime && " $(id -nG "$(id -un)" 2>/dev/null) " =~ \ lifetime-(admin|console)\  ]]; then
        hidden=''
    fi
    {
        printf '[Desktop Entry]\nType=Application\nName=Lifetime Server\nComment=Lifetime Server dashboard\nExec='
        desktop_quote "$install_dir/LifetimeServerDashboard"
        printf "\nIcon=%s\nTerminal=false\n${hidden}Categories=Utility;\n" "$install_dir/assets/lifetime-server.png"
    } > "$data_home/applications/lifetime-server.desktop"
fi
printf 'Open Lifetime to choose what to collect and whether it starts automatically with the computer. This installer does not start tracking.\n'
printf 'Installed Lifetime in %s. Remove it with uninstall_linux.sh.\n' "$install_dir"
