#!/usr/bin/env bash
set -euo pipefail

### =========================
### Configuration
### =========================

VERSION="0.1.0"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_real_home() {
    if [[ -n "${POKETERM_REAL_HOME:-}" ]]; then
        printf '%s\n' "$POKETERM_REAL_HOME"
        return 0
    fi

    local user_name home_path
    user_name="${SUDO_USER:-${USER:-$(id -un 2>/dev/null || echo root)}}"

    if command -v dscl >/dev/null 2>&1; then
        home_path="$(dscl . -read "/Users/$user_name" NFSHomeDirectory 2>/dev/null | awk '{print $2}' | tail -n 1)"
    fi

    if [[ -z "$home_path" ]] && command -v getent >/dev/null 2>&1; then
        home_path="$(getent passwd "$user_name" 2>/dev/null | cut -d: -f6)"
    fi

    if [[ -z "$home_path" ]]; then
        home_path="${HOME:-$PWD}"
    fi

    printf '%s\n' "$home_path"
}

REAL_HOME="$(resolve_real_home)"
if [[ -n "${ZSHRC:-}" && "${ZSHRC}" == /private/tmp/* ]]; then
    ZSHRC="${REAL_HOME}/.zshrc"
fi

INSTALL_ROOT="${POKETERM_INSTALL_ROOT:-${REAL_HOME}/.local}"
BIN_DIR="${POKETERM_BIN_DIR:-${REAL_HOME}/.local/bin}"
ZSHRC="${POKETERM_ZSHRC:-${ZSHRC:-${REAL_HOME}/.zshrc}}"

INSTALL_DIR="$INSTALL_ROOT/share/"
ZSHRC_MARKER_BEGIN="# >>> POKETERM SCRIPT BEGIN >>>"
ZSHRC_MARKER_END="# <<< POKETERM SCRIPT END <<<"

LOCAL_USER="${SUDO_USER:-${USER:-$(logname 2>/dev/null || echo root)}}"
POKEDEX_FILE="$INSTALL_DIR/poketerm/pokedex.txt"

UPDATE=false
UNINSTALL=false
GEN_SPECIFIC=false
GEN=""

ensure_zshrc_exists() {
    if [[ -z "$ZSHRC" ]]; then
        return 0
    fi

    if [[ ! -f "$ZSHRC" ]]; then
        if ! touch "$ZSHRC" 2>/dev/null; then
            echo "Unable to write to $ZSHRC; Homebrew is sandboxed and cannot modify your shell startup file here."
            echo "Run: bash ./install.sh --zshrc \"$HOME/.zshrc\" to enable poketerm in your normal shell."
            ZSHRC=""
            return 0
        fi
    fi
}

### =========================
### Flag parsing
### =========================

while [ $# -gt 0 ]; do
    case "$1" in
        --install-root)
            INSTALL_ROOT="$2"
            shift 2
            ;;
        --bin-dir)
            BIN_DIR="$2"
            shift 2
            ;;
        --zshrc)
            ZSHRC="$2"
            shift 2
            ;;
        --gen)
            GEN_SPECIFIC=true
            gen="$2"
            shift 2

            [[ "$gen" =~ ^[0-9]+(-[0-9]+)?$ ]] || {
                echo "Error: --gen must be N or N-M (1–8)"
                exit 1
            }

            IFS=- read -r start end <<< "$gen"
            end=${end:-$start}

            (( start >= 1 && end <= 8 && start <= end )) || {
                echo "Error: generations must be between 1 and 8"
                exit 1
            }

            GEN="$gen"
            ;;
        --install|--setup)
            UPDATE=false
            UNINSTALL=false
            shift
            ;;
        --update|--upgrade)
            UPDATE=true
            UNINSTALL=false
            shift
            ;;
        --uninstall|--remove)
            UNINSTALL=true
            UPDATE=false
            shift
            ;;
        -h|--help)
            echo "Usage: ./install.sh [--install|--update|--uninstall] [--gen N|N-M]"
            exit 0
            ;;
        *)
            echo "Unknown flag: $1"
            exit 1
            ;;
    esac
done

INSTALL_DIR="$INSTALL_ROOT/share/"
POKEDEX_FILE="$INSTALL_DIR/poketerm/pokedex.txt"

### =========================
### Helpers
### =========================

has_existing_install() {
    [[ -d "$INSTALL_DIR/poketerm" ]] || [[ -L "$BIN_DIR/poketerm" ]] || return 1
    return 0
}

current_files_dir() {
    if [[ -d "$REPO_ROOT/files/current" ]]; then
        echo "$REPO_ROOT/files/current"
        return 0
    fi

    if [[ -d "$REPO_ROOT/files/$VERSION" ]]; then
        echo "$REPO_ROOT/files/$VERSION"
        return 0
    fi

    echo "$REPO_ROOT/files" >&2
    return 1
}

ensure_dirs() {
    ensure_zshrc_exists

    if [[ -z "$ZSHRC" ]]; then
        mkdir -p "$INSTALL_DIR/poketerm"
        touch "$INSTALL_DIR/poketerm/pokedex.txt"
        return 0
    fi

    if grep -Fxq "$ZSHRC_MARKER_BEGIN" "$ZSHRC"; then
        local filtered
        filtered=$(mktemp)
        awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
            BEGIN { skip = 0 }
            $0 == begin { skip = 1; next }
            skip && $0 == end { skip = 0; next }
            !skip { print }
        ' "$ZSHRC" > "$filtered"
        mv "$filtered" "$ZSHRC"
    else
        mkdir -p "$INSTALL_DIR/poketerm"
        if [[ ! -f "$INSTALL_DIR/poketerm/pokedex.txt" ]]; then
            touch "$INSTALL_DIR/poketerm/pokedex.txt"
        fi

        if [[ -n "$ZSHRC" && -f "$ZSHRC" ]]; then
            echo "Backing up ~/.zshrc to poketerm directory $(pwd)/zshrc.backup. Please keep this file for the uninstall script."
            cp "$ZSHRC" ./zshrc.backup
        fi
    fi

    if [[ -n "$ZSHRC" ]]; then
        echo "Appending Pokemon bootstrap to the end of ~/.zshrc"
        if ! {
            printf '\n%s\n' "$ZSHRC_MARKER_BEGIN"
            printf 'export PATH="%s:$PATH"\n' "$BIN_DIR"
            if command -v brew >/dev/null 2>&1; then
                printf 'brew_bin="$(brew --prefix 2>/dev/null)/bin"\n'
                printf 'if [[ -n "$brew_bin" && -d "$brew_bin" ]]; then export PATH="$brew_bin:$PATH"; fi\n'
            fi
            if command -v neowofetch >/dev/null 2>&1; then
                printf 'alias neofetch=neowofetch\n'
            elif command -v neofetch >/dev/null 2>&1; then
                printf 'alias neofetch=neofetch\n'
            fi
            printf 'if command -v poketerm >/dev/null 2>&1; then\n'
            if [[ "$GEN_SPECIFIC" == true ]]; then
                printf '  poketerm --gen %s\n' "$gen"
            else
                printf '  poketerm\n'
            fi
            printf 'fi\n'
            printf '%s\n' "$ZSHRC_MARKER_END"
        } >> "$ZSHRC" 2>/dev/null; then
            echo "Unable to write the shell hook to $ZSHRC because Homebrew is running inside a sandbox."
            echo "Run: bash ./install.sh --zshrc \"$HOME/.zshrc\" in your regular shell to enable poketerm startup."
            ZSHRC=""
        fi
    else
        echo "Homebrew install detected; skipping shell startup hook injection in the sandboxed home directory."
    fi
}

install_hyfetch() {
    # Install hyfetch
    if ! command -v hyfetch >/dev/null 2>&1; then
        echo "Hyfetch not found. Installing via Homebrew..."
        yes | brew install hyfetch >/dev/null 2>&1 || true
        brew link --overwrite hyfetch >/dev/null 2>&1 || true
    else
        echo "hyfetch is already installed."
    fi
}

install_neofetch() {
    if ! command -v neofetch >/dev/null 2>&1 && ! command -v neowofetch >/dev/null 2>&1; then
        echo "Neither neofetch nor neowofetch was found. Installing via Homebrew..."
        if command -v brew >/dev/null 2>&1; then
            yes | brew install neofetch >/dev/null 2>&1 || true
        fi
    fi
}

install_pokemon_colorscripts() {
    # Install pokemon-colorscripts into the active user bin dir instead of a hardcoded /usr/local.
    if ! command -v pokemon-colorscripts >/dev/null 2>&1; then
        echo "pokemon-colorscripts not found. Installing..."
        mkdir -p "$BIN_DIR"
        rm -rf "$INSTALL_DIR"pokemon-colorscripts || return 1
        git clone https://gitlab.com/phoneybadger/pokemon-colorscripts.git "$INSTALL_DIR"pokemon-colorscripts >/dev/null 2>&1

        if [[ -f "$INSTALL_DIR"pokemon-colorscripts/pokemon-colorscripts.py ]]; then
            chmod +x "$INSTALL_DIR"pokemon-colorscripts/pokemon-colorscripts.py
            ln -sf "$INSTALL_DIR"pokemon-colorscripts/pokemon-colorscripts.py "$BIN_DIR/pokemon-colorscripts"
            chmod +x "$BIN_DIR/pokemon-colorscripts"
        else
            echo "pokemon-colorscripts install failed: expected script not found in clone."
            return 1
        fi
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

    local app_files
    app_files="$(current_files_dir)" || {
        echo "No current app files found under $REPO_ROOT/files" >&2
        exit 1
    }

    local preserved_pokedex=""
    local preserved_trainer=""

    if [[ -f "$POKEDEX_FILE" ]]; then
        preserved_pokedex="$(mktemp)"
        cp "$POKEDEX_FILE" "$preserved_pokedex"
    fi

    if [[ -f "$INSTALL_DIR/poketerm/trainer.json" ]]; then
        preserved_trainer="$(mktemp)"
        cp "$INSTALL_DIR/poketerm/trainer.json" "$preserved_trainer"
    fi

    cp -rf "$REPO_ROOT/files/gen_files" "$INSTALL_DIR/poketerm"
    cp -rf "$REPO_ROOT/files/cache" "$INSTALL_DIR/poketerm"
    cp -r "$app_files"/* "$INSTALL_DIR/poketerm"

    if [[ -n "$preserved_pokedex" ]]; then
        cp "$preserved_pokedex" "$POKEDEX_FILE"
        rm -f "$preserved_pokedex"
    fi

    if [[ -n "$preserved_trainer" ]]; then
        cp "$preserved_trainer" "$INSTALL_DIR/poketerm/trainer.json"
        rm -f "$preserved_trainer"
    fi

    chmod +x "$INSTALL_DIR/poketerm/pokedex.py" "$INSTALL_DIR/poketerm/poketerm"

    mkdir -p "$BIN_DIR"
    rm -rf "$BIN_DIR/pokedex" "$BIN_DIR/poketerm" || return 1
    ln -s "$INSTALL_DIR/poketerm/poketerm" "$BIN_DIR/poketerm"
    ln -s "$INSTALL_DIR/poketerm/pokedex.py" "$BIN_DIR/pokedex"

    install_hyfetch
    install_neofetch
    install_pokemon_colorscripts

    echo "Creating pokedex integrity file"
    touch "$POKEDEX_FILE.sha256"
    POKEDEX_HASH="$POKEDEX_FILE.sha256"

    if [ ! -s "$POKEDEX_FILE" ]; then
        if command -v sha256sum >/dev/null 2>&1; then
            sha256sum "$POKEDEX_FILE" > "$POKEDEX_HASH"
        else
            shasum -a 256 "$POKEDEX_FILE" > "$POKEDEX_HASH"
        fi
    fi

    chmod 444 "$POKEDEX_FILE"

    echo "-------------------------------------------------------"
    echo " Poketerm installed successfully!"
    echo "-------------------------------------------------------"

    if [[ -n "$ZSHRC" ]]; then
        echo "To start collecting pokemon run: source $ZSHRC"
    else
        echo "Poketerm is installed. Restart your shell or run: poketerm"
    fi
}

install_or_update() {
    if ! has_existing_install; then
        echo "No existing Poketerm install detected. Installing $VERSION..."
        install
        return 0
    fi

    echo "Poketerm is already installed. Refreshing the current install..."
    install
    return 0
}

### =========================
### Current-version compatibility
### =========================

update() {
    if ! has_existing_install; then
        echo "No existing install detected. Installing $VERSION..."
        install
        return 0
    fi

    echo "Applying the current-version compatibility update..."
    install
    return 0
}

### =========================
### Uninstall
### =========================

uninstall() {
    # Remove poketerm and pokemon-colorscripts directories
    echo "Removing poketerm and pokemon-colorscripts shared directories"
    if [[ -f "$INSTALL_DIR"pokemon-colorscripts/uninstall.sh ]]; then
        "$INSTALL_DIR"pokemon-colorscripts/uninstall.sh
    fi
    rm -rf "$INSTALL_DIR/poketerm" "$INSTALL_DIR/pokemon-colorscripts" || return 1

    # Uninstall hyfetch if it was installed via Homebrew
    if command -v hyfetch >/dev/null 2>&1; then
        echo "Uninstalling hyfetch via brew"
        brew unlink hyfetch >/dev/null 2>&1
        brew uninstall hyfetch >/dev/null 2>&1
    fi

    # If zshrc.backup file exists from when poketerm was installed, restore it
    if [[ -n "$ZSHRC" && -f "./zshrc.backup" ]]; then
        echo "Restoring original ~/.zshrc from poketerm/zshrc.backup"
        cp ./zshrc.backup "$ZSHRC"
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
    install_or_update
fi
