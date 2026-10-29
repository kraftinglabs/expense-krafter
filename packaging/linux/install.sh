#!/bin/sh
# Install ExpenseKrafter for the current user (no root needed):
#   ./install.sh              copy the app to ~/.local/opt/ExpenseKrafter, add it to
#                             the app menu and put an `expensekrafter` command on PATH
#   ./install.sh --uninstall  remove all of that again
# Your data (~/Documents/ExpenseKrafter) and settings (~/.config/ExpenseKrafter)
# are never touched by either.
set -eu

APP_DIR="$HOME/.local/opt/ExpenseKrafter"
BIN="$HOME/.local/bin/expensekrafter"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/expensekrafter.desktop"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$APP_DIR"
    rm -f "$BIN" "$DESKTOP"
    echo "ExpenseKrafter removed. Your data in ~/Documents/ExpenseKrafter was kept."
    exit 0
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$HERE" != "$APP_DIR" ]; then
    rm -rf "$APP_DIR"
    mkdir -p "$(dirname "$APP_DIR")"
    cp -a "$HERE" "$APP_DIR"
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$DESKTOP")"
ln -sf "$APP_DIR/ExpenseKrafter" "$BIN"
sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/expensekrafter.desktop" > "$DESKTOP"
chmod +x "$DESKTOP"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$(dirname "$DESKTOP")" || true

echo "ExpenseKrafter installed: find it in your app menu, or run 'expensekrafter'."
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "(Add ~/.local/bin to your PATH to use the command.)" ;; esac
