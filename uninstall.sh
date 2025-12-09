#!/bin/sh

INSTALL_DIR="$HOME/.local/share/"
BIN_DIR='/usr/local/bin'
ZSHRC="$HOME/.zshrc"
LOCAL_USER="${SUDO_USER:-$(logname)}"

# Remove poketerm and pokemon-colorscripts directories
echo "Removing poketerm and pokemon-colorscripts shared directories"
rm -rf "$INSTALL_DIR/poketerm" "$INSTALL_DIR/pokemon-colorscripts" || return 1

# Uninstall hyfetch if it was installed via Homebrew
if command -v hyfetch >/dev/null 2>&1; then
    echo "Uninstalling hyfetch via brew"
    sudo -u "$LOCAL_USER" brew unlink hyfetch >/dev/null 2>&1
    sudo -u "$LOCAL_USER" brew uninstall hyfetch >/dev/null 2>&1
fi

# If zshrc.backup file exists from when poketerm was installed, restore it
if [ -f "./zshrc.backup" ]; then
    echo "Restoring original ~/.zshrc from poketerm/zshrc.backup"
    sudo -u "$LOCAL_USER" cp ./zshrc.backup $ZSHRC
fi
