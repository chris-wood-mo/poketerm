#!/bin/sh

UPDATE=false

# Parse flags
for arg in "$@"; do
    case $arg in
        --update)
            UPDATE=true
            shift
            ;;
        *)
            ;;
    esac
done

INSTALL_DIR="$HOME/.local/share/"
BIN_DIR='/usr/local/bin'
ZSHRC="$HOME/.zshrc"
ZSHRC_MARKER_BEGIN="# >>> POKETERM SCRIPT BEGIN >>>"
ZSHRC_MARKER_END="# <<< POKETERM SCRIPT END <<<"
LOCAL_USER="${SUDO_USER:-$(logname)}"

# deleting directory if it already exists
if ! grep -Fxq "$ZSHRC_MARKER_BEGIN" "$ZSHRC"; then
    rm -rf "$INSTALL_DIR/poketerm" || return 1
    
    # Ensure run directory exists and clean it up
    sudo -u "$LOCAL_USER" mkdir -p "$INSTALL_DIR/poketerm"

    # Create pokedex file
    sudo -u "$LOCAL_USER" touch "$INSTALL_DIR/poketerm/pokedex.txt"
fi

# moving all the files to appropriate locations
sudo -u "$LOCAL_USER" cp -rf gen_files $INSTALL_DIR/poketerm
sudo -u "$LOCAL_USER" cp files/pokedex $INSTALL_DIR/poketerm
sudo -u "$LOCAL_USER" chmod +x $INSTALL_DIR/poketerm/pokedex

# create symlink in usr/bin
rm -rf "$BIN_DIR/pokedex" || return 1
ln -s $INSTALL_DIR/poketerm/pokedex $BIN_DIR/pokedex

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
    echo "pokemon-colorscripts is already installed."
fi

if [ "$UPDATE" = true ]; then
    echo "Updating existing Pokémon script in ~/.zshrc"

    sudo -u "$LOCAL_USER" cp "$ZSHRC" ./zshrc_update.backup
    sudo -u "$LOCAL_USER" touch "$ZSHRC.tmp"
    TMP_ZSHRC="$ZSHRC.tmp"

    # Use awk to replace between markers, reading snippet from file
    awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
        BEGIN {reading_snippet=0}
        $0 == begin {
            print begin
            while ((getline line < "./files/zshrc") > 0) print line
            reading_snippet=1
            next
        }
        $0 == end {reading_snippet=0; print end; next}
        reading_snippet != 1 {print}
    ' "$ZSHRC" > "$TMP_ZSHRC"

    mv "$TMP_ZSHRC" "$ZSHRC"

    # Make sure file exists
    if [ ! -f "$INSTALL_DIR/poketerm/pokedex.txt" ]; then
        echo "No pokedex file found at $INSTALL_DIR/poketerm/pokedex.txt"
        exit 1
    fi

    echo "Backing up ~/.zshrc to poketerm directory $(pwd)/pokedex_update.backup. If you have any issues with the update please the pokedex at $INSTALL_DIR/poketerm/pokedex.txt using this backup."
    sudo -u "$LOCAL_USER" cp "$INSTALL_DIR/poketerm/pokedex.txt" ./pokedex_update.backup
    sudo -u "$LOCAL_USER" touch "format_file.tmp"
    FORMAT_FILE="format_file.tmp"

    awk '
    {
        # Check for duplicates
        if ($0 in seen) {
            print "Error: duplicate Pokémon entry found -> " $0 > "/dev/stderr"
            exit 1
        }
        seen[$0]=1

        # Append count if missing
        if ($NF ~ /^[0-9]+$/) {
            print $0  # Already has count, leave as-is
        } else {
            print $0, 1
        }
    }
    ' "$INSTALL_DIR/poketerm/pokedex.txt" > "$FORMAT_FILE"

    if [ $? -eq 0 ]; then
        mv "$FORMAT_FILE" "$INSTALL_DIR/poketerm/pokedex.txt"
        echo "Poketerm updated successfully!"
    else
        echo "Pokedex contains duplicates this should not be possible. Fix the file to only contain one of each pokemon and try again."
        rm -f "$FORMAT_FILE"
        exit 1
    fi
else
    if ! grep -Fxq "$ZSHRC_MARKER_BEGIN" "$ZSHRC"; then
        echo "Backing up ~/.zshrc to poketerm directory $(pwd)/zshrc.backup. Please keep this file for the uninstall script."
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
        echo "Poketerm installed successfully! To start collecting pokemon run: source zshrc"
    else
         echo "Pokémon script already exists in ~/.zshrc (skipping install), use --update flag if you need to update the script"
    fi
fi
