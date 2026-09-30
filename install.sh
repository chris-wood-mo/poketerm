#!/usr/bin/env bash
set -euo pipefail

### =========================
### Configuration
### =========================

VERSION="0.0.7"
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

if [[ -n "${POKETERM_INSTALL_ROOT+x}" ]]; then
    INSTALL_ROOT="${POKETERM_INSTALL_ROOT}"
else
    INSTALL_ROOT="${REAL_HOME}/.local"
fi

if [[ -n "${POKETERM_BIN_DIR+x}" ]]; then
    BIN_DIR="${POKETERM_BIN_DIR}"
else
    BIN_DIR="${REAL_HOME}/.local/bin"
fi

if [[ -n "${POKETERM_ZSHRC+x}" ]]; then
    ZSHRC="${POKETERM_ZSHRC}"
elif [[ -n "${ZSHRC:-}" ]]; then
    ZSHRC="${ZSHRC}"
else
    ZSHRC="${REAL_HOME}/.zshrc"
fi

INSTALL_DIR="$INSTALL_ROOT/share/"
ZSHRC_MARKER_BEGIN="# >>> POKETERM SCRIPT BEGIN >>>"
ZSHRC_MARKER_END="# <<< POKETERM SCRIPT END <<<"

LOCAL_USER="${SUDO_USER:-${USER:-$(logname 2>/dev/null || echo root)}}"
POKEDEX_FILE="$INSTALL_DIR/poketerm/pokedex.txt"

UPDATE=false
UNINSTALL=false
GEN_SPECIFIC=false
GEN_OVERRIDE_ACTIVE=false
GEN=""
ZSHRC_EXPLICIT=false

ensure_zshrc_exists() {
    if [[ -z "$ZSHRC" ]]; then
        return 0
    fi

    if [[ ! -f "$ZSHRC" ]]; then
        if ! touch "$ZSHRC" 2>/dev/null; then
            echo "Unable to write to $ZSHRC. Check the install path and permissions."
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
ZSHRC_BACKUP_PATH="$INSTALL_DIR/poketerm/zshrc.backup"
POKEDEX_FILE="$INSTALL_DIR/poketerm/pokedex.txt"

if [[ "$GEN_SPECIFIC" == true ]]; then
    GEN_OVERRIDE_ACTIVE=true
fi

if [[ "$ZSHRC_EXPLICIT" == false && "${ZSHRC:-}" == "${REAL_HOME}/.zshrc" && "${HOME:-}" != "${REAL_HOME}" ]]; then
    ZSHRC=""
fi

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

remove_zshrc_hook() {
    if [[ -z "$ZSHRC" || ! -f "$ZSHRC" ]]; then
        return 0
    fi

    local filtered
    filtered=$(mktemp)
    awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
        BEGIN { skip = 0; prev_blank = 0 }
        $0 ~ "^[[:space:]]*# >>> POKETERM SCRIPT BEGIN >>>[[:space:]]*$" { skip = 1; next }
        skip && $0 ~ "^[[:space:]]*# <<< POKETERM SCRIPT END <<<[[:space:]]*$" { skip = 0; next }
        skip { next }
        /^[[:space:]]*$/ {
            if (prev_blank) {
                next
            }
            prev_blank = 1
            print ""
            next
        }
        {
            prev_blank = 0
            print
        }
    ' "$ZSHRC" > "$filtered"
    mv "$filtered" "$ZSHRC"
}

backup_progress_files() {
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

    printf '%s\n%s\n' "$preserved_pokedex" "$preserved_trainer"
}

restore_progress_files() {
    local preserved_pokedex="$1"
    local preserved_trainer="$2"

    if [[ -n "$preserved_pokedex" ]]; then
        chmod u+w "$POKEDEX_FILE" 2>/dev/null || true
        cp "$preserved_pokedex" "$POKEDEX_FILE"
        rm -f "$preserved_pokedex"
    fi

    if [[ -n "$preserved_trainer" ]]; then
        chmod u+w "$INSTALL_DIR/poketerm/trainer.json" 2>/dev/null || true
        cp "$preserved_trainer" "$INSTALL_DIR/poketerm/trainer.json"
        rm -f "$preserved_trainer"
    fi
}

ensure_dirs() {
    ensure_zshrc_exists

    if [[ -z "$ZSHRC" ]]; then
        mkdir -p "$INSTALL_DIR/poketerm"
        touch "$INSTALL_DIR/poketerm/pokedex.txt"
        return 0
    fi

    mkdir -p "$INSTALL_DIR/poketerm"

    if [[ ! -f "$INSTALL_DIR/poketerm/pokedex.txt" ]]; then
        touch "$INSTALL_DIR/poketerm/pokedex.txt"
    fi

    if grep -Eq "^# >>> POKETERM SCRIPT BEGIN >>>$|^# <<< POKETERM SCRIPT END <<<\$" "$ZSHRC"; then
        remove_zshrc_hook
    fi

    echo "Appending Pokemon bootstrap to the end of ~/.zshrc"
    if ! {
        printf '\n%s\n' "$ZSHRC_MARKER_BEGIN"
        printf 'export PATH="%s:$PATH"\n' "$BIN_DIR"
        printf 'if command -v poketerm >/dev/null 2>&1; then\n'
        if [[ "$GEN_OVERRIDE_ACTIVE" == true ]]; then
            printf '  poketerm --gen %s\n' "$gen"
        else
            printf '  poketerm\n'
        fi
        printf 'fi\n'
        printf '%s\n' "$ZSHRC_MARKER_END"
    } >> "$ZSHRC" 2>/dev/null; then
        echo "Unable to write the shell hook to $ZSHRC. Check the target file permissions and try again."
        echo "Run: bash ./install.sh --zshrc \"$HOME/.zshrc\" in your regular shell to enable poketerm startup."
        ZSHRC=""
    fi
}

show_spinner() {
    local pid="$1"
    local delay="0.8"
    local phases=(
        "Adding Pokemon"
        "Creating Poke Regions"
        "Getting the trainers ready for battle"
        "Finding some Legendary Pokemon"
    )

    for phase in "${phases[@]}"; do
        printf '\r\033[K  %s...' "$phase"
        sleep "$delay"
    done

    while kill -0 "$pid" 2>/dev/null; do
        sleep 0.1
    done

    printf '\r\033[K'
}

install_hyfetch() {
    if ! command -v hyfetch >/dev/null 2>&1; then
        echo "Hyfetch not found. Install it before running poketerm if you want the optional hyfetch integration."
    else
        echo "hyfetch is already installed."
    fi
}

install_neofetch() {
    if ! command -v neofetch >/dev/null 2>&1 && ! command -v neowofetch >/dev/null 2>&1; then
        echo "Neither neofetch nor neowofetch was found. Install one of them before running poketerm."
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
    local install_log
    install_log="$(mktemp)"

    (
        ensure_dirs >/dev/null 2>&1

        local app_files
        app_files="$(current_files_dir)" || {
            echo "No current app files found under $REPO_ROOT/files" >&2
            exit 1
        }

        local preserved_pokedex=""
        local preserved_trainer=""
        local -a progress_paths=()
        while IFS= read -r line; do
            progress_paths+=("$line")
        done < <(backup_progress_files)

        preserved_pokedex="${progress_paths[0]:-}"
        preserved_trainer="${progress_paths[1]:-}"

        find "$INSTALL_DIR/poketerm" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
        cp -rf "$REPO_ROOT/files/gen_files" "$INSTALL_DIR/poketerm"
        cp -rf "$REPO_ROOT/files/cache" "$INSTALL_DIR/poketerm"
        cp -r "$app_files"/* "$INSTALL_DIR/poketerm"
        find "$INSTALL_DIR/poketerm" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

        restore_progress_files "$preserved_pokedex" "$preserved_trainer"

        chmod +x "$INSTALL_DIR/poketerm/pokedex.py" "$INSTALL_DIR/poketerm/poketerm"

        mkdir -p "$BIN_DIR"
        rm -rf "$BIN_DIR/pokedex" "$BIN_DIR/poketerm" || return 1
        ln -s "$INSTALL_DIR/poketerm/poketerm" "$BIN_DIR/poketerm"
        ln -s "$INSTALL_DIR/poketerm/pokedex.py" "$BIN_DIR/pokedex"

        install_hyfetch >"$install_log" 2>&1 || true
        install_neofetch >"$install_log" 2>&1 || true
        install_pokemon_colorscripts >"$install_log" 2>&1 || true

        printf '%s\n' "$VERSION" > "$INSTALL_DIR/poketerm/VERSION"

        touch "$POKEDEX_FILE.sha256"
        POKEDEX_HASH="$POKEDEX_FILE.sha256"

        if [ ! -s "$POKEDEX_FILE" ]; then
            if command -v sha256sum >/dev/null 2>&1; then
                sha256sum "$POKEDEX_FILE" > "$POKEDEX_HASH"
            else
                shasum -a 256 "$POKEDEX_FILE" > "$POKEDEX_HASH"
            fi
        fi
    ) >/dev/null 2>&1 &

    local install_pid=$!
    show_spinner "$install_pid"
    wait "$install_pid"

    rm -f "$install_log"
}

show_install_success() {
    echo "-------------------------------------------------------"
    echo " Poketerm installed successfully!"
    echo "-------------------------------------------------------"
    echo ""
    echo "🚀 Ready to catch Pokemons!"
    echo "Run: source $ZSHRC"
}

show_upgrade_success() {
    echo "-------------------------------------------------------"
    echo " Poketerm upgraded to version $VERSION"
    echo "-------------------------------------------------------"
    echo ""
    echo "🎉 You're all ready to keep catching Pokemon trainer!"
    echo "Run: source $ZSHRC"
}

install_or_update() {
    if ! has_existing_install; then
        echo "No existing Poketerm install detected. Installing $VERSION..."
        install
        show_install_success
        return 0
    fi

    echo "Poketerm is already installed. Refreshing the current install..."
    install
    show_install_success
    return 0
}

### =========================
### Current-version compatibility
### =========================

update() {
    if ! has_existing_install; then
        echo "No existing install detected. Installing $VERSION..."
        install
        show_install_success
        return 0
    fi

    echo "Applying the current-version compatibility update..."
    install
    show_upgrade_success
    return 0
}

### =========================
### Uninstall
### =========================

uninstall() {
    echo "Removing Poketerm files and app data..."

    if [[ -f "$INSTALL_DIR"pokemon-colorscripts/uninstall.sh ]]; then
        "$INSTALL_DIR"pokemon-colorscripts/uninstall.sh
    fi
    rm -rf "$INSTALL_DIR/poketerm" "$INSTALL_DIR/pokemon-colorscripts" || return 1

    rm -f "$BIN_DIR/poketerm" "$BIN_DIR/pokedex" "$BIN_DIR/pokemon-colorscripts"

    if [[ -n "$ZSHRC" ]]; then
        echo ""
        echo "⚠️  Please remove the Poketerm block from: $ZSHRC"
        echo "Look for the lines beginning with: # >>> POKETERM SCRIPT BEGIN >>>"
        echo "and ending with: # <<< POKETERM SCRIPT END <<<"
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
