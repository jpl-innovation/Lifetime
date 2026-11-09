#!/usr/bin/env bash
set -euo pipefail
company_agent=false
case "${1:-}" in
    '') ;;
    --company-agent) company_agent=true ;;
    *) printf 'Usage: install_linux.sh [--company-agent]\n'; exit 2 ;;
esac
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="$HOME/.local/share/lifetime"
python_bin="$(command -v python3 || true)"
if [[ -z "$python_bin" ]]; then
    printf 'Python 3 is required. Install it with your package manager (for example: sudo apt install python3), then run this installer again.\n'
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
# Journal access is optional. Never invoke sudo without an affirmative answer.
if command -v journalctl >/dev/null 2>&1; then
    journal_test="$(LC_ALL=C journalctl --system -n 1 --no-pager 2>&1)" || journal_test="permission denied"
    if [[ "$journal_test" == *"not seeing messages"* || "$journal_test" == *"permission denied"* || "$journal_test" == *"insufficient permissions"* ]]; then
        printf 'Reading the full system journal lets Lifetime add the time this computer was used before you installed it. Adding your user to systemd-journal requires approval and a new login.\n'
        read -r -p 'Run sudo usermod -aG systemd-journal for your user? (y/N) ' answer || answer=''
        if [[ "$answer" == 'y' || "$answer" == 'Y' ]]; then
            sudo usermod -aG systemd-journal "$(id -un)" || printf 'Journal access unchanged; earlier usage may be incomplete.\n'
        else
            printf 'Continuing without journal access changes; earlier usage may be incomplete.\n'
        fi
    fi
fi
mkdir -p "$install_dir/assets" "$HOME/.config/systemd/user" "$HOME/.local/share/applications"
for file in lifetime_tracker.py lifetime_gui.py lifetime_health.py lifetime_health_gui.py lifetime_shell.py lifetime_signals.py lifetime_dashboard.py lifetime_charts.py lifetime_settings.py lifetime_setup.py lifetime_import.py lifetime_import_windows.py lifetime_daily.py lifetime_i18n.py lifetime_i18n_qt.py lifetime_reports.py lifetime_technician.py lifetime_ledger.py lifetime_identity.py lifetime_theme.py lifetime_tasks.py lifetime_company.py lifetime_connect.py lifetime_agent.py lifetime_live.py lifetime_reveal.py lifetime_ws.py lifetime_ws_client.py lifetime_client.py lifetime_protocol.py lifetime_tls.py lifetime_permissions.py lifetime_connection.py lifetime_discovery.py; do
    if [[ "$source_dir/$file" != "$install_dir/$file" ]]; then
        install -m 644 "$source_dir/$file" "$install_dir/$file"
    fi
done
# Escape paths for systemd's quoted argument syntax and percent specifiers.
unit_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    printf '"%s"' "$value"
}
{
    printf '[Unit]\nDescription=Lifetime awake-time tracker\n\n[Service]\nType=simple\nExecStart='
    unit_quote "$python_bin"
    printf ' '
    unit_quote "$install_dir/lifetime_tracker.py"
    printf ' --run\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n'
} > "$HOME/.config/systemd/user/lifetime-tracker.service"
if [[ "$company_agent" == true ]]; then
    printf 'The company agent records a separate awake-time ledger while you are signed in. Reporting needs separate consent and an approved connection code in Company server. Optional history and system readings stay off.\n'
    read -r -p 'Install and start the company-agent user service? (y/N) ' answer || answer=''
    if [[ "$answer" == 'y' || "$answer" == 'Y' ]]; then
        agent_dir="${XDG_STATE_HOME:-$HOME/.local/state}/lifetime-agent"
        if [[ ! -f "$agent_dir/machine.sqlite" ]]; then
            "$python_bin" "$install_dir/lifetime_agent.py" --directory "$agent_dir" --initialize
        fi
        {
            printf '[Unit]\nDescription=Lifetime company reporting agent\n\n[Service]\nType=simple\nExecStart='
            unit_quote "$python_bin"
            printf ' '
            unit_quote "$install_dir/lifetime_agent.py"
            printf ' --directory '
            unit_quote "$agent_dir"
            printf ' --run\nRestart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n'
        } > "$HOME/.config/systemd/user/lifetime-company-agent.service"
        systemctl --user daemon-reload
        systemctl --user enable --now lifetime-company-agent.service
    fi
fi
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
{
    printf '[Desktop Entry]\nType=Application\nName=Lifetime\nComment=Total computer awake time\nExec='
    desktop_quote "$python_bin"
    printf ' '
    desktop_quote "$install_dir/lifetime_gui.py"
    printf '\nIcon=%s\nTerminal=false\nCategories=Utility;\n' "$install_dir/assets/lifetime.png"
} > "$HOME/.local/share/applications/lifetime.desktop"
install -m 644 "$source_dir/assets/lifetime.png" "$install_dir/assets/lifetime.png"
install -m 644 "$source_dir/assets/jpl_innovation.png" "$install_dir/assets/jpl_innovation.png"
systemctl --user daemon-reload
printf 'Open Lifetime to choose what to collect and whether to track at login. No tracking is started by this installer.\n'
printf 'Installed Lifetime. The GUI requires PyQt6 for %s.\n' "$python_bin"
