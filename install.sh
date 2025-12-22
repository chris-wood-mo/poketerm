#!/usr/bin/env bash
set -euo pipefail

### =========================
### Configuration
### =========================

VERSION="0.0.3"

INSTALL_DIR="$HOME/.local/share/"
BIN_DIR="/usr/local/bin"
ZSHRC="$HOME/.zshrc"

ZSHRC_MARKER_BEGIN="# >>> POKETERM SCRIPT BEGIN >>>"
ZSHRC_MARKER_END="# <<< POKETERM SCRIPT END <<<"

VERSION_FILE="$INSTALL_DIR/poketerm/VERSION"
LOCAL_USER="${SUDO_USER:-$(logname)}"
POKEDEX_FILE="$HOME/.local/share/poketerm/pokedex.txt"

UPDATE=false
UNINSTALL=false

### =========================
### Flag parsing
### =========================

for arg in "$@"; do
    case "$arg" in
        --update) UPDATE=true ;;
        --uninstall) UNINSTALL=true ;;
        *)
            echo "Unknown flag: $arg"
            exit 1
            ;;
    esac
done

### =========================
### Helpers
### =========================

write_version() {
    local version="$1"
    sudo -u "$LOCAL_USER" touch "$VERSION_FILE"
    echo $version > "$VERSION_FILE"
}


installed_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        cat "$VERSION_FILE"
        return
    fi

    if command -v git >/dev/null 2>&1; then
        if git -C "$(dirname "$0")" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            tag="$(git -C "$(dirname "$0")" describe --tags --abbrev=0 2>/dev/null || true)"
            if [[ -n "$tag" ]]; then
                echo "$tag"
                write_version $tag
                return
            fi
        fi
    fi

    echo "0.0.0"
}

ensure_dirs() {
    if ! grep -Fxq "$ZSHRC_MARKER_BEGIN" "$ZSHRC"; then
        # deleting directory if it already exists
        rm -rf "$INSTALL_DIR/poketerm" || return 1

        # Ensure run directory exists and clean it up
        sudo -u "$LOCAL_USER" mkdir -p "$INSTALL_DIR/poketerm"

        # Create pokedex file
        sudo -u "$LOCAL_USER" touch "$INSTALL_DIR/poketerm/pokedex.txt"

        echo "Backing up ~/.zshrc to poketerm directory $(pwd)/zshrc.backup. Please keep this file for the uninstall script."

        sudo -u "$LOCAL_USER" cp "$ZSHRC" ./zshrc.backup

        echo "Prepending Pokemon script into ~/.zshrc"

        sudo -u "$LOCAL_USER" touch "$ZSHRC.tmp"
        {
            echo "$ZSHRC_MARKER_BEGIN"
            echo "alias neofetch=neowofetch"
            echo "poketerm"
            echo "$ZSHRC_MARKER_END"
            echo ""
            cat "$ZSHRC"
        } >> "$ZSHRC.tmp"

        mv "$ZSHRC.tmp" "$ZSHRC"
    fi
}

install_hyfetch() {
    # Install hyfetch
    if ! command -v hyfetch >/dev/null 2>&1; then
        echo "Hyfetch not found. Installing via Homebrew..."
        sudo -u "$LOCAL_USER" brew install hyfetch >/dev/null 2>&1
        sudo -u "$LOCAL_USER" brew link --overwrite hyfetch >/dev/null 2>&1
    else
        echo "hyfetch is already installed."
    fi
}

install_pokemon_colorscripts() {
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
}

### =========================
### Install
### =========================

install() {
    echo "Installing poketerm $VERSION"

    ensure_dirs

   # moving all the files to appropriate locations
    sudo -u "$LOCAL_USER" cp -rf files/gen_files $INSTALL_DIR/poketerm
    sudo -u "$LOCAL_USER" cp files/$VERSION/pokedex files/$VERSION/poketerm $INSTALL_DIR/poketerm
    sudo -u "$LOCAL_USER" chmod +x $INSTALL_DIR/poketerm/pokedex $INSTALL_DIR/poketerm/poketerm

    # create symlink in usr/bin
    rm -rf $BIN_DIR/pokedex $BIN_DIR/poketerm || return 1
    ln -s $INSTALL_DIR/poketerm/poketerm $BIN_DIR/poketerm
    ln -s $INSTALL_DIR/poketerm/pokedex $BIN_DIR/pokedex

    install_hyfetch
    install_pokemon_colorscripts

    echo "Creating pokedex integrity file"
    sudo -u "$LOCAL_USER" touch "$POKEDEX_FILE.sha256"
    POKEDEX_HASH="$POKEDEX_FILE.sha256"

    if [ ! -s "$POKEDEX_FILE" ]; then
        if command -v sha256sum >/dev/null 2>&1; then
            sha256sum "$POKEDEX_FILE" > "$POKEDEX_HASH"
        else
            shasum -a 256 "$POKEDEX_FILE" > "$POKEDEX_HASH"
        fi
    fi

    chmod 444 "$POKEDEX_FILE"

    write_version $VERSION
    echo "Poketerm installed successfully! To start collecting pokemon run: source zshrc"
}

### =========================
### Migrations
### =========================

migrate_001_to_002() {
    echo "Migrating 0.0.1 → 0.0.2"
    echo "Updating existing Pokémon script in ~/.zshrc"

    sudo -u "$LOCAL_USER" cp "$ZSHRC" ./zshrc_update.backup
    sudo -u "$LOCAL_USER" cp files/0.0.2/pokedex $INSTALL_DIR/poketerm
    sudo -u "$LOCAL_USER" chmod +x $INSTALL_DIR/poketerm/pokedex
    sudo -u "$LOCAL_USER" touch "$ZSHRC.tmp"
    TMP_ZSHRC="$ZSHRC.tmp"

    # Use awk to replace between markers, reading snippet from file
    awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
        BEGIN {reading_snippet=0}
        $0 == begin {
            print begin
            while ((getline line < "./files/0.0.2/zshrc") > 0) print line
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

    echo "Backing up ~/.zshrc to poketerm directory $(pwd)/pokedex_update.backup. If you have any issues with the update please the pokedex at ${INSTALL_DIR}poketerm/pokedex.txt using this backup."
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
        echo "Poketerm updated successfully from 0.0.1 to 0.0.2!"
    else
        echo "Pokedex contains duplicates this should not be possible. Fix the file to only contain one of each pokemon and try again."
        rm -f "$FORMAT_FILE"
        exit 1
    fi

    write_version "0.0.2"
}

migrate_002_to_003() {
    echo "Migrating 0.0.2 → 0.0.3"

    sudo -u "$LOCAL_USER" cp files/$VERSION/poketerm $INSTALL_DIR/poketerm
    sudo -u "$LOCAL_USER" chmod +x $INSTALL_DIR/poketerm/poketerm

    # create symlink in usr/bin
    rm -rf $BIN_DIR/poketerm || return 1
    ln -s $INSTALL_DIR/poketerm/poketerm $BIN_DIR/poketerm

    echo "Updating existing Pokémon script in ~/.zshrc"

    sudo -u "$LOCAL_USER" cp "$ZSHRC" ./zshrc_update.backup
    sudo -u "$LOCAL_USER" touch "$ZSHRC.tmp"
    TMP_ZSHRC="$ZSHRC.tmp"

    # Use awk to replace between markers, reading snippet from file
    awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
        BEGIN {reading_snippet=0}
        $0 == begin {
            print begin
            print "alias neofetch=neowofetch"
            print "poketerm"
            reading_snippet=1
            next
        }
        $0 == end {reading_snippet=0; print end; next}
        reading_snippet != 1 {print}
    ' "$ZSHRC" > "$TMP_ZSHRC"

    mv "$TMP_ZSHRC" "$ZSHRC"

    POKEDEX_HASH="$POKEDEX_FILE.sha256"
    if [ -s "$POKEDEX_FILE" ] && [ ! -f "$POKEDEX_HASH" ]; then
        echo "Creating pokedex integrity file for 0.0.3"

        sudo -u "$LOCAL_USER" touch "$POKEDEX_HASH"
        if command -v sha256sum >/dev/null 2>&1; then
            sha256sum "$POKEDEX_FILE" > "$POKEDEX_HASH"
        else
            shasum -a 256 "$POKEDEX_FILE" > "$POKEDEX_HASH"
        fi

        chmod 444 "$POKEDEX_FILE"
    fi

    echo "Poketerm updated successfully from 0.0.2 to 0.0.3!"

    write_version "0.0.3"
}

### =========================
### Update
### =========================

update() {
    local current
    current="$(installed_version)"

    echo "Installed version: $current"

    case "$current" in
        0.0.1)
            echo "Target version:    0.0.2"
            migrate_001_to_002
            ;;
        0.0.2)
            echo "Target version:    0.0.3"
            migrate_002_to_003
            ;;
        0.0.3)
            echo "Already up to date."
            exit 0
            ;;
        *)
            echo "Unsupported update path: $current → $VERSION"
            echo "Please reinstall."
            exit 1
            ;;
    esac
}

### =========================
### Uninstall
### =========================

uninstall() {
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
}

### =========================
### Entry point
### =========================

if [[ "$UNINSTALL" == true ]]; then
    uninstall
elif [[ "$UPDATE" == true ]]; then
    update
else
    install
fi
