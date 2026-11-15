#!/usr/bin/env bash
# Remove Lifetime for the current user: the program, its menu entry and its login start (systemd user services and
# XDG autostart entries). Your awake-time history in ~/.local/share/lifetime is kept unless you add --delete-data.
set -euo pipefail
delete_data=false
case "${1:-}" in
    '') ;;
    --delete-data) delete_data=true ;;
    *) printf 'Usage: uninstall_linux.sh [--delete-data]\n'; exit 2 ;;
esac
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now lifetime-tracker.service lifetime-company-agent.service >/dev/null 2>&1 || true
fi
rm -f "$config_home/systemd/user/lifetime-tracker.service" "$config_home/systemd/user/lifetime-company-agent.service" \
      "$config_home/autostart/lifetime-tracker.desktop" "$config_home/autostart/lifetime-company-agent.desktop" \
      "$config_home/autostart/lifetime-tray.desktop" "$data_home/applications/lifetime.desktop"
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi
# Stop running copies of this user's Lifetime programs (never anyone else's).
for name in LifetimeTracker LifetimeAgent Lifetime; do
    pkill -u "$(id -u)" -x "$name" 2>/dev/null || true
done
if [[ -L "$HOME/.local/bin/lifetime" ]]; then
    rm -f "$HOME/.local/bin/lifetime"
fi
rm -rf "$HOME/.local/lib/lifetime" "$HOME/.local/lib/lifetime.new" "$HOME/.local/lib/lifetime.previous"
if [[ "$delete_data" == true ]]; then
    rm -rf "$HOME/.local/share/lifetime" "${XDG_STATE_HOME:-$HOME/.local/state}/lifetime-agent"
    printf 'Lifetime and its data were removed.\n'
else
    # A source install keeps its program files in the data folder: remove only those.
    rm -f "$HOME"/.local/share/lifetime/lifetime_*.py
    rm -rf "$HOME/.local/share/lifetime/assets" "$HOME/.local/share/lifetime/__pycache__"
    printf 'Lifetime was removed. Your history is kept in %s (uninstall_linux.sh --delete-data removes it).\n' "$HOME/.local/share/lifetime"
fi
