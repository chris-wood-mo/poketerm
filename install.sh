#!/bin/sh

INSTALL_DIR="$HOME/.local/share/"
BIN_DIR='/usr/local/bin'
ZSHRC="$HOME/.zshrc"
ZSHRC_MARKER_BEGIN="# >>> POKETERM SCRIPT BEGIN >>>"
ZSHRC_MARKER_END="# <<< POKETERM SCRIPT END <<<"
LOCAL_USER="${SUDO_USER:-$(logname)}"

# deleting directory if it already exists
rm -rf "$INSTALL_DIR/poketerm" || return 1

# Ensure run directory exists and clean it up
sudo -u "$LOCAL_USER" mkdir -p "$INSTALL_DIR/poketerm"

# Install hyfetch
if ! command -v hyfetch >/dev/null 2>&1; then
    echo "Hyfetch not found. Installing via Homebrew..."
    sudo -u "$LOCAL_USER" brew install hyfetch >/dev/null 2>&1
    sudo -u "$LOCAL_USER" brew link --overwrite hyfetch >/dev/null 2>&1
else
    echo "hyfetch is already installed."
fi

# Install pokemon-colorscripts
if ! command -v pokemon-colorscripts >/dev/null 2>&1; then
    rm -rf "$INSTALL_DIR"pokemon-colorscripts || return 1
    git clone https://gitlab.com/phoneybadger/pokemon-colorscripts.git "$INSTALL_DIR"pokemon-colorscripts
    cd "$INSTALL_DIR"pokemon-colorscripts
    sudo "$INSTALL_DIR"pokemon-colorscripts/install.sh
    cd -
else
    echo "pmokemon-colorscripts is already installed."
fi

# moving all the files to appropriate locations
sudo -u "$LOCAL_USER" cp -rf gen_files $INSTALL_DIR/poketerm
sudo -u "$LOCAL_USER" cp files/pokedex $INSTALL_DIR/poketerm
sudo -u "$LOCAL_USER" touch "$INSTALL_DIR/poketerm/pokedex.txt"
sudo -u "$LOCAL_USER" chmod +x $INSTALL_DIR/poketerm/pokedex

# create symlink in usr/bin
rm -rf "$BIN_DIR/pokedex" || return 1
ln -s $INSTALL_DIR/poketerm/pokedex $BIN_DIR/pokedex

# add zhrc snippet if it doesn't already exist
if ! grep -Fxq "$ZSHRC_MARKER_BEGIN" "$ZSHRC"; then
    echo "Backing up ~/.zshrc to poketerm directory $(pwd)/zshrc.backup. Please keep this file for the uninstall script."
    rm "./zshrc.backup" || return 1
    sudo -u "$LOCAL_USER" cp "$ZSHRC" ./zshrc.backup

    echo "Prepending Pokémon script into ~/.zshrc"

    sudo -u "$LOCAL_USER" touch "$ZSHRC.tmp"
    {
        echo "$ZSHRC_MARKER_BEGIN"
        cat ./files/zshrc
        echo "$ZSHRC_MARKER_END"
        echo ""
        cat "$ZSHRC"
    } >> "$ZSHRC.tmp"

    mv "$ZSHRC.tmp" "$ZSHRC"
else
    echo "Pokémon script already exists in ~/.zshrc"
fi
