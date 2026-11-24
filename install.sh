#!/usr/bin/env bash
set -Eeuo pipefail

### =========================
### Configuration
### =========================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Older versions told users to run `sudo ./install.sh`. Poketerm lives entirely
# in the user's home, so running as root would leave root-owned files the user
# can't write (and XP would silently stop saving). Re-run as the real user.
if [[ "${EUID:-$(id -u)}" -eq 0 && -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then
    echo "Poketerm doesn't need sudo - continuing as $SUDO_USER."
    exec sudo -u "$SUDO_USER" -H bash "$REPO_ROOT/install.sh" "$@"
fi

VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null || true)"
VERSION="${VERSION:-0.0.0}"

ZSHRC_MARKER_BEGIN="# >>> POKETERM SCRIPT BEGIN >>>"
ZSHRC_MARKER_END="# <<< POKETERM SCRIPT END <<<"
BACKUPS_TO_KEEP=5

resolve_real_home() {
    if [[ -n "${POKETERM_REAL_HOME:-}" ]]; then
        printf '%s\n' "$POKETERM_REAL_HOME"
        return 0
    fi

    local user_name home_path=""
    user_name="${SUDO_USER:-${USER:-$(id -un 2>/dev/null || echo root)}}"

    if command -v dscl >/dev/null 2>&1; then
        home_path="$(dscl . -read "/Users/$user_name" NFSHomeDirectory 2>/dev/null | awk '{print $2}' | tail -n 1)"
    fi

    if [[ -z "$home_path" ]] && command -v getent >/dev/null 2>&1; then
        home_path="$(getent passwd "$user_name" 2>/dev/null | cut -d: -f6)"
    fi

    printf '%s\n' "${home_path:-${HOME:-$PWD}}"
}

REAL_HOME="$(resolve_real_home)"
INSTALL_ROOT="${POKETERM_INSTALL_ROOT:-$REAL_HOME/.local}"
BIN_DIR="${POKETERM_BIN_DIR:-$REAL_HOME/.local/bin}"
ZSHRC="${POKETERM_ZSHRC:-${ZSHRC:-$REAL_HOME/.zshrc}}"
if [[ "$ZSHRC" == /private/tmp/* && -z "${POKETERM_ZSHRC:-}" ]]; then
    ZSHRC="$REAL_HOME/.zshrc"
fi

MODE=install          # install | update | uninstall
DEV=false
GEN=""
LOG_FILE=""

usage() {
    cat <<EOF
Poketerm installer (version $VERSION)

Usage: ./install.sh [--install|--update|--uninstall] [OPTIONS]

  --install          Install poketerm (default; refreshes an existing install)
  --update           Update an existing install, keeping all progress
  --uninstall        Remove poketerm and its data
  --gen N|N-M        Only catch Pokemon from generation N (or N to M), 1-8
  --dev              Developer mode: link to this checkout so edits are live
  --zshrc PATH       Use a different zshrc file
  -h, --help         Show this help

Most users can simply run:  poketerm --update
EOF
}

### =========================
### Flag parsing
### =========================

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gen)
            [[ $# -ge 2 ]] || { echo "Error: --gen needs a value (N or N-M)"; exit 1; }
            GEN="$2"
            [[ "$GEN" =~ ^[0-9]+(-[0-9]+)?$ ]] || { echo "Error: --gen must be N or N-M (1-8)"; exit 1; }
            IFS=- read -r gen_start gen_end <<< "$GEN"
            gen_end=${gen_end:-$gen_start}
            (( gen_start >= 1 && gen_end <= 8 && gen_start <= gen_end )) || {
                echo "Error: generations must be between 1 and 8"
                exit 1
            }
            shift 2
            ;;
        --install|--setup)     MODE=install; shift ;;
        --update|--upgrade)    MODE=update; shift ;;
        --uninstall|--remove)  MODE=uninstall; shift ;;
        --dev)                 DEV=true; shift ;;
        --zshrc)
            [[ $# -ge 2 ]] || { echo "Error: --zshrc needs a path"; exit 1; }
            ZSHRC="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown flag: $1"; usage; exit 1 ;;
    esac
done

APP_DIR="$INSTALL_ROOT/share/poketerm"
SHARE_DIR="$INSTALL_ROOT/share"
POKEDEX_FILE="$APP_DIR/pokedex.txt"
POKEDEX_HASH="$POKEDEX_FILE.sha256"

### =========================
### Output helpers
### =========================

if [[ -t 1 ]]; then
    C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_RESET=$'\033[0m'
    C_CLEAR=$'\r\033[K'
else
    C_GREEN=""; C_RED=""; C_YELLOW=""; C_RESET=""; C_CLEAR=""
fi

CURRENT_STEP=""
step() {
    CURRENT_STEP="$1"
    [[ -t 1 ]] && printf '  • %s...' "$1"
    return 0
}
ok() {
    printf '%s  %s✔%s %s\n' "$C_CLEAR" "$C_GREEN" "$C_RESET" "$CURRENT_STEP"
    CURRENT_STEP=""
}
warn() {
    printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$1"
}

on_error() {
    local status=$?
    trap - ERR
    [[ -n "$CURRENT_STEP" ]] && printf '%s  %s✖%s %s\n' "$C_CLEAR" "$C_RED" "$C_RESET" "$CURRENT_STEP"
    rollback_code_files || true
    echo ""
    echo "${C_RED}Poketerm $MODE failed.${C_RESET} Your Pokedex and trainer progress were not changed."
    [[ -n "${LAST_BACKUP:-}" ]] && echo "A copy of your progress is saved in: $LAST_BACKUP"
    if [[ -n "$LOG_FILE" && -s "$LOG_FILE" ]]; then
        echo "Details:"
        tail -n 15 "$LOG_FILE" | sed 's/^/    /'
        echo "Full log: $LOG_FILE"
    fi
    exit "$status"
}

run() {
    # Run a command, sending its output to the log instead of the screen.
    "$@" >>"$LOG_FILE" 2>&1
}

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1"
    else
        shasum -a 256 "$1"
    fi
}

### =========================
### Checks
### =========================

app_files_dir() {
    if [[ -d "$REPO_ROOT/files/current" ]]; then
        echo "$REPO_ROOT/files/current"
    elif [[ -d "$REPO_ROOT/files/$VERSION" ]]; then
        echo "$REPO_ROOT/files/$VERSION"
    else
        return 1
    fi
}

preflight() {
    step "Checking requirements"
    local missing=()
    command -v zsh >/dev/null 2>&1 || missing+=("zsh")
    command -v python3 >/dev/null 2>&1 || missing+=("python3")
    command -v git >/dev/null 2>&1 || command -v pokemon-colorscripts >/dev/null 2>&1 || missing+=("git")
    if (( ${#missing[@]} )); then
        printf '%s' "$C_CLEAR"
        echo "${C_RED}Missing required tools:${C_RESET} ${missing[*]}"
        echo "Install them and run the installer again."
        exit 1
    fi
    app_files_dir >/dev/null || { echo "No app files found under $REPO_ROOT/files" >>"$LOG_FILE"; false; }

    # Installs made with `sudo` by old versions can contain root-owned files.
    # Stop before changing anything and say exactly how to fix it.
    if [[ -d "$APP_DIR" ]]; then
        local not_mine
        not_mine="$(find "$APP_DIR" -maxdepth 2 ! -user "$(id -un)" -print -quit 2>/dev/null || true)"
        if [[ -n "$not_mine" ]]; then
            printf '%s' "$C_CLEAR"
            echo "${C_RED}Some poketerm files are owned by another user (probably from an old 'sudo' install):${C_RESET}"
            echo "    $not_mine"
            echo "Fix it with this command, then run the installer again (your progress is safe):"
            echo "    sudo chown -R $(id -un) \"$APP_DIR\""
            exit 1
        fi
    fi
    ok
    if ! command -v neofetch >/dev/null 2>&1 && ! command -v neowofetch >/dev/null 2>&1; then
        warn "neofetch not found - system details will be hidden until it is installed (e.g. brew install neofetch)"
    fi
}

has_existing_install() {
    [[ -d "$APP_DIR" && -f "$APP_DIR/poketerm" ]] || [[ -L "$BIN_DIR/poketerm" ]]
}

installed_version() {
    tr -d '[:space:]' < "$APP_DIR/VERSION" 2>/dev/null || echo "unknown"
}

### =========================
### Progress backup
### =========================

LAST_BACKUP=""

backup_progress() {
    [[ -f "$POKEDEX_FILE" || -f "$APP_DIR/trainer.json" ]] || return 0

    step "Backing up your Pokedex and trainer progress"
    local stamp dest
    stamp="$(date +%Y%m%d-%H%M%S)"
    dest="$APP_DIR/backups/$stamp-$(installed_version)"
    run mkdir -p "$dest"
    for f in pokedex.txt pokedex.txt.sha256 trainer.json; do
        [[ -f "$APP_DIR/$f" ]] && run cp -p "$APP_DIR/$f" "$dest/$f"
    done
    LAST_BACKUP="$dest"

    # Keep only the most recent backups.
    local old
    while IFS= read -r old; do
        [[ -n "$old" && "$old" == "$APP_DIR/backups/"* ]] && run rm -rf -- "$old"
    done < <(ls -1d "$APP_DIR"/backups/*/ 2>/dev/null | sort -r | tail -n +$((BACKUPS_TO_KEEP + 1)))
    ok
}

### =========================
### Code install (staged, with rollback)
### =========================

STAGE_DIR=""
ROLLBACK_DIR=""
SWAPPED_FILES=()

cleanup_stage() {
    [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]] && rm -rf -- "$STAGE_DIR"
    STAGE_DIR=""
}
trap cleanup_stage EXIT

rollback_code_files() {
    (( ${#SWAPPED_FILES[@]} )) || return 0
    local f
    for f in "${SWAPPED_FILES[@]}"; do
        if [[ -e "$ROLLBACK_DIR/$f" || -L "$ROLLBACK_DIR/$f" ]]; then
            mv -f "$ROLLBACK_DIR/$f" "$APP_DIR/$f"
        else
            rm -f -- "$APP_DIR/$f"
        fi
    done
    echo "  Restored the previous version's files."
    SWAPPED_FILES=()
}

install_code() {
    local src
    src="$(app_files_dir)"

    step "Preparing poketerm $VERSION"
    run mkdir -p "$APP_DIR" "$SHARE_DIR"
    STAGE_DIR="$(mktemp -d "$SHARE_DIR/.poketerm-stage.XXXXXX")"
    ROLLBACK_DIR="$STAGE_DIR/rollback"
    run mkdir -p "$STAGE_DIR/new" "$ROLLBACK_DIR"

    # Code files only. User data (pokedex.txt, trainer.json, cache) is never
    # copied over; trainer.json in the repo is only a template for new installs.
    local -a code_files=()
    local path name
    for path in "$src"/*; do
        name="$(basename "$path")"
        [[ -f "$path" ]] || continue
        [[ "$name" == "trainer.json" ]] && continue
        code_files+=("$name")
        if [[ "$DEV" == true ]]; then
            run ln -s "$path" "$STAGE_DIR/new/$name"
        else
            run cp -p "$path" "$STAGE_DIR/new/$name"
        fi
    done
    [[ ${#code_files[@]} -gt 0 ]] || { echo "No code files found in $src" >>"$LOG_FILE"; false; }
    [[ "$DEV" == true ]] || run chmod +x "$STAGE_DIR/new/poketerm" "$STAGE_DIR/new/pokedex.py" "$STAGE_DIR/new/update.sh"
    ok

    step "Installing poketerm $VERSION"
    for name in "${code_files[@]}"; do
        if [[ -e "$APP_DIR/$name" || -L "$APP_DIR/$name" ]]; then
            run cp -pP "$APP_DIR/$name" "$ROLLBACK_DIR/$name"
        fi
        SWAPPED_FILES+=("$name")
        # rename() within one filesystem is atomic: a terminal opening mid-update
        # sees either the old file or the new one, never a half-written file.
        run mv -f "$STAGE_DIR/new/$name" "$APP_DIR/$name"
    done

    # Remove code files that the previous version shipped but this one doesn't.
    if [[ -f "$APP_DIR/.manifest" ]]; then
        local old
        while IFS= read -r old; do
            [[ -n "$old" && "$old" != */* ]] || continue
            printf '%s\n' "${code_files[@]}" | grep -qxF "$old" && continue
            run rm -f -- "$APP_DIR/$old"
        done < "$APP_DIR/.manifest"
    fi
    printf '%s\n' "${code_files[@]}" > "$APP_DIR/.manifest"

    run cp -R "$REPO_ROOT/files/gen_files" "$APP_DIR/"
    run mkdir -p "$APP_DIR/cache"
    run cp -R "$REPO_ROOT/files/cache/." "$APP_DIR/cache/"
    find "$APP_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

    SWAPPED_FILES=()   # committed; nothing to roll back past this point
    ok
}

seed_user_data() {
    if [[ ! -f "$APP_DIR/trainer.json" ]]; then
        cp "$(app_files_dir)/trainer.json" "$APP_DIR/trainer.json"
    fi

    if [[ ! -f "$POKEDEX_FILE" ]]; then
        touch "$POKEDEX_FILE"
        chmod 444 "$POKEDEX_FILE"
    fi

    # Pokedexes from 0.0.1/0.0.2 stored one name per line with no count
    # ("pikachu" instead of "pikachu 1"). Convert them, merging any duplicate
    # lines, so they work with the current format. Already backed up above.
    local migrated=false
    if grep -qvE '[[:space:]][0-9]+[[:space:]]*$|^[[:space:]]*$' "$POKEDEX_FILE"; then
        local converted
        converted="$(mktemp "$APP_DIR/.pokedex.XXXXXX")"
        awk '
            /^[[:space:]]*$/ { next }
            {
                line = $0
                sub(/[[:space:]]+$/, "", line)
                count = 1
                if (match(line, /[[:space:]][0-9]+$/)) {
                    count = substr(line, RSTART + 1) + 0
                    line = substr(line, 1, RSTART - 1)
                    sub(/[[:space:]]+$/, "", line)
                }
                if (!(line in total)) order[++n] = line
                total[line] += count
            }
            END { for (i = 1; i <= n; i++) print order[i], total[order[i]] }
        ' "$POKEDEX_FILE" > "$converted"
        chmod 444 "$converted"
        mv -f "$converted" "$POKEDEX_FILE"
        migrated=true
    fi

    # Older versions could leave an empty or missing hash, which made every new
    # terminal report the Pokedex as tampered with.
    if [[ "$migrated" == true || ! -s "$POKEDEX_HASH" ]]; then
        sha256_file "$POKEDEX_FILE" > "$POKEDEX_HASH"
    fi
}

link_commands() {
    step "Linking the poketerm and pokedex commands"
    run mkdir -p "$BIN_DIR"
    run ln -sfn "$APP_DIR/poketerm" "$BIN_DIR/poketerm"
    run ln -sfn "$APP_DIR/pokedex.py" "$BIN_DIR/pokedex"
    ok
}

install_pokemon_colorscripts() {
    command -v pokemon-colorscripts >/dev/null 2>&1 && return 0
    [[ -x "$BIN_DIR/pokemon-colorscripts" ]] && return 0

    step "Installing pokemon-colorscripts"
    local dest="$SHARE_DIR/pokemon-colorscripts"
    [[ -d "$dest" ]] && run rm -rf -- "$dest"
    run git clone --depth 1 https://gitlab.com/phoneybadger/pokemon-colorscripts.git "$dest"
    [[ -f "$dest/pokemon-colorscripts.py" ]] || { echo "pokemon-colorscripts.py missing after clone" >>"$LOG_FILE"; false; }
    run chmod +x "$dest/pokemon-colorscripts.py"
    run ln -sfn "$dest/pokemon-colorscripts.py" "$BIN_DIR/pokemon-colorscripts"
    ok
}

write_install_info() {
    if [[ "$DEV" == true ]]; then
        printf '%s+dev\n' "$VERSION" > "$APP_DIR/VERSION"
    else
        printf '%s\n' "$VERSION" > "$APP_DIR/VERSION"
    fi

    # Read by `poketerm --update` so updates land in the same place.
    {
        printf 'BIN_DIR=%s\n' "$BIN_DIR"
        printf 'ZSHRC=%s\n' "$ZSHRC"
        printf 'MODE=%s\n' "$([[ "$DEV" == true ]] && echo dev || echo release)"
        [[ "$DEV" == true ]] && printf 'SOURCE=%s\n' "$REPO_ROOT"
    } > "$APP_DIR/install.conf"

    # Clear any cached "update available" notice.
    rm -f "$APP_DIR/.update_check"
}

### =========================
### Shell hook
### =========================

has_hook() {
    [[ -f "$ZSHRC" ]] && grep -qxF "$ZSHRC_MARKER_BEGIN" "$ZSHRC"
}

remove_zshrc_hook() {
    [[ -f "$ZSHRC" ]] || return 0
    local filtered
    filtered="$(mktemp)"
    # Also drops the blank line(s) the installer put around the block, so
    # moving the block doesn't leave gaps behind.
    awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
        $0 == begin { skip = 1; blanks = 0; next }
        skip && $0 == end { skip = 0; after = 1; next }
        skip { next }
        /^[[:space:]]*$/ { if (!(after && !printed)) blanks++; next }
        { while (blanks > 0) { print ""; blanks-- } print; printed = 1; after = 0 }
    ' "$ZSHRC" > "$filtered"
    cat "$filtered" > "$ZSHRC"   # keep the file's owner/permissions/symlink
    rm -f "$filtered"
}

hook_block() {
    awk -v begin="$ZSHRC_MARKER_BEGIN" -v end="$ZSHRC_MARKER_END" '
        $0 == begin { inside = 1; next }
        $0 == end   { inside = 0 }
        inside { print }
    ' "$ZSHRC"
}

# Line where the hook has to go so it runs before Powerlevel10k's instant
# prompt (the comment lines directly above its `if` included), or nothing if
# the zshrc doesn't use instant prompt. p10k warns about any console output
# printed after that block, so poketerm's output must come before it.
p10k_insert_line() {
    [[ -f "$ZSHRC" ]] || return 0
    awk '
        /^[[:space:]]*#/ { if (!c) c = NR; next }
        /p10k-instant-prompt/ { print (c ? c : NR); exit }
        { c = 0 }
    ' "$ZSHRC"
}

hook_line() {
    grep -nxF "$ZSHRC_MARKER_BEGIN" "$ZSHRC" 2>/dev/null | head -n 1 | cut -d: -f1
}

ensure_zshrc_hook() {
    # On update, keep the user's existing hook (and their --gen choice) unless
    # a new --gen was given, it's from an old version, or it sits below
    # Powerlevel10k's instant prompt.
    if has_hook && [[ -z "$GEN" ]]; then
        local block p10k_at
        block="$(hook_block)"
        p10k_at="$(p10k_insert_line)"
        if grep -q '^export PATH=' <<< "$block" \
           && { [[ -z "$p10k_at" ]] || (( $(hook_line) < p10k_at )); }; then
            return 0
        fi
        GEN="$(sed -nE 's/.*poketerm --gen ([0-9]+(-[0-9]+)?).*/\1/p' <<< "$block" | head -n 1)"
    fi

    step "Adding poketerm to $ZSHRC"
    if ! touch "$ZSHRC" 2>>"$LOG_FILE"; then
        printf '%s' "$C_CLEAR"
        warn "Could not write to $ZSHRC. Run: ./install.sh --zshrc /path/to/.zshrc"
        CURRENT_STEP=""
        return 0
    fi
    cp -p "$ZSHRC" "$APP_DIR/zshrc.backup" 2>/dev/null || true
    remove_zshrc_hook

    local hook_file
    hook_file="$(mktemp)"
    {
        printf '%s\n' "$ZSHRC_MARKER_BEGIN"
        printf 'export PATH="%s:$PATH"\n' "$BIN_DIR"
        printf 'if command -v poketerm >/dev/null 2>&1; then\n'
        if [[ -n "$GEN" ]]; then
            printf '  poketerm --gen %s\n' "$GEN"
        else
            printf '  poketerm\n'
        fi
        printf 'fi\n'
        printf '%s\n' "$ZSHRC_MARKER_END"
    } > "$hook_file"

    local insert_at
    insert_at="$(p10k_insert_line)"
    if [[ -n "$insert_at" ]]; then
        local merged
        merged="$(mktemp)"
        awk -v n="$insert_at" -v f="$hook_file" '
            NR == n { while ((getline l < f) > 0) print l; print "" }
            { print }
        ' "$ZSHRC" > "$merged"
        cat "$merged" > "$ZSHRC"
        rm -f "$merged"
    else
        { printf '\n'; cat "$hook_file"; } >> "$ZSHRC"
    fi
    rm -f "$hook_file"
    ok
}

### =========================
### Entry points
### =========================

do_install() {
    local previous="" first_install=true hook_existed=false
    if has_existing_install; then
        first_install=false
        previous="$(installed_version)"
    fi
    has_hook && hook_existed=true

    if [[ "$MODE" == update && "$first_install" == true ]]; then
        echo "No existing Poketerm install found - installing $VERSION instead."
    elif [[ "$first_install" == true ]]; then
        echo "Installing Poketerm $VERSION"
    else
        echo "Updating Poketerm ${previous} -> ${VERSION}$([[ "$DEV" == true ]] && echo " (dev mode)")"
    fi

    LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/poketerm-install.XXXXXX")"
    trap on_error ERR

    preflight
    backup_progress
    install_code
    seed_user_data
    link_commands
    install_pokemon_colorscripts
    write_install_info
    ensure_zshrc_hook

    trap - ERR
    rm -f "$LOG_FILE"

    echo ""
    if [[ "$first_install" == true ]]; then
        echo "${C_GREEN}Poketerm $VERSION installed!${C_RESET} Ready to catch Pokemon."
        echo "Open a new terminal (or run: source $ZSHRC) to catch your first one."
    else
        echo "${C_GREEN}Poketerm is now on $VERSION.${C_RESET} Your Pokedex and trainer progress were kept."
        if [[ "$hook_existed" == true ]]; then
            echo "Open a new terminal to see it."
        else
            echo "Open a new terminal (or run: source $ZSHRC) to see it."
        fi
    fi
    if [[ "$DEV" == true ]]; then
        echo "Dev mode: code is linked to $REPO_ROOT/files/current - edits apply to the next terminal."
    fi
}

do_uninstall() {
    echo "Removing Poketerm files and app data..."

    if [[ -f "$SHARE_DIR/pokemon-colorscripts/uninstall.sh" ]]; then
        "$SHARE_DIR/pokemon-colorscripts/uninstall.sh" || true
    fi
    [[ -n "$INSTALL_ROOT" && -d "$APP_DIR" ]] && rm -rf -- "$APP_DIR"
    [[ -n "$INSTALL_ROOT" && -d "$SHARE_DIR/pokemon-colorscripts" ]] && rm -rf -- "$SHARE_DIR/pokemon-colorscripts"
    rm -f "$BIN_DIR/poketerm" "$BIN_DIR/pokedex" "$BIN_DIR/pokemon-colorscripts"

    if has_hook; then
        echo ""
        echo "⚠️  Please remove the Poketerm block from: $ZSHRC"
        echo "Look for the lines beginning with: $ZSHRC_MARKER_BEGIN"
        echo "and ending with: $ZSHRC_MARKER_END"
    fi
}

if [[ "$MODE" == uninstall ]]; then
    do_uninstall
else
    do_install
fi
