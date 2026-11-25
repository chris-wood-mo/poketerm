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
    # python3 is only needed where poketerm can't bring its own (see install_python_runtime).
    if ! command -v python3 >/dev/null 2>&1; then
        { [[ -n "$(python_runtime_target)" ]] && command -v curl >/dev/null 2>&1; } \
            || [[ -x "$PYTHON_RUNTIME_DIR/bin/python3" ]] || missing+=("python3")
    fi
    command -v git >/dev/null 2>&1 || command -v curl >/dev/null 2>&1 \
        || command -v pokemon-colorscripts >/dev/null 2>&1 || missing+=("curl or git")
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
    if ! command -v fastfetch >/dev/null 2>&1 && ! command -v neofetch >/dev/null 2>&1 && ! command -v neowofetch >/dev/null 2>&1; then
        local how="brew install fastfetch"
        if [[ "$(uname -s)" != Darwin ]]; then
            if command -v apt-get >/dev/null 2>&1; then how="sudo apt install fastfetch, or neofetch on Ubuntu 24.04 / Debian 12 and older"
            elif command -v dnf >/dev/null 2>&1; then how="sudo dnf install fastfetch"
            elif command -v pacman >/dev/null 2>&1; then how="sudo pacman -S fastfetch"
            elif command -v zypper >/dev/null 2>&1; then how="sudo zypper install fastfetch"
            else how="see https://github.com/fastfetch-cli/fastfetch"
            fi
        fi
        warn "fastfetch not found - system details will be hidden until it is installed ($how)"
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
    # Trading identity, history and trainers met. -p keeps the private key 0600.
    if [[ -d "$APP_DIR/trade" ]]; then
        run cp -Rp "$APP_DIR/trade" "$dest/trade"
        run chmod 700 "$dest"
    fi
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
    [[ "$DEV" == true ]] || run chmod +x "$STAGE_DIR/new/poketerm" "$STAGE_DIR/new/pokedex" "$STAGE_DIR/new/pokedex.py" "$STAGE_DIR/new/update.sh"
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
    run ln -sfn "$APP_DIR/pokedex" "$BIN_DIR/pokedex"
    ok
}

# --- Python runtime ---------------------------------------------------------
# poketerm runs on its own pinned Python, so it behaves the same on every
# machine whatever Python (if any) the system has - e.g. trading needs TLS 1.3,
# which macOS's built-in Python 3.9 lacks.
# Builds: https://github.com/astral-sh/python-build-standalone (MPL-2.0), the
# self-contained Python builds used by uv. Pinned to a reviewed release; the
# hashes below match both GitHub's digests and the release's SHA256SUMS, and a
# download that doesn't match is rejected. To upgrade: review the release, then
# update the version, release AND all four hashes together.
PYTHON_RUNTIME_VERSION="3.14.10"
PYTHON_RUNTIME_RELEASE="20261026"
PYTHON_RUNTIME_DIR="$SHARE_DIR/poketerm-python"
python_runtime_target() {
    # The build's target triple for this machine, or nothing if there isn't one.
    local arch
    case "$(uname -m)" in
        x86_64|amd64)  arch=x86_64 ;;
        arm64|aarch64) arch=aarch64 ;;
        *) return 0 ;;
    esac
    case "$(uname -s)" in
        Darwin) echo "$arch-apple-darwin" ;;
        Linux)
            # These builds need glibc; musl systems (e.g. Alpine) use their own python3.
            if ldd --version 2>&1 | grep -qi musl; then return 0; fi
            echo "$arch-unknown-linux-gnu" ;;
    esac
}
python_runtime_sha256() {
    case "$1" in
        aarch64-apple-darwin)      echo "95eb8c87d16af37c54e9765e31d8c56c0d8ffa9968ea0f704f73a9b83f84e9f9" ;;
        x86_64-apple-darwin)       echo "2fd8becd48cf0ff5f0a15df3c0db7cae7aa708dcaacf901bd30d6cdd6229370b" ;;
        x86_64-unknown-linux-gnu)  echo "4d3c16c59ed0802a3d1d34d8bce504b15ceeaff39d62dadecc6a9fa803412aed" ;;
        aarch64-unknown-linux-gnu) echo "90f48337bc3dd4ae6eda81e001e4c981236d403b6b5a07778bbdf94ee423dc00" ;;
    esac
}
python_runtime_id() { echo "$PYTHON_RUNTIME_VERSION+$PYTHON_RUNTIME_RELEASE"; }
RUNTIME_REQUIREMENTS="$REPO_ROOT/files/runtime-requirements.txt"

# Packages trading uses (cryptography), from files/runtime-requirements.txt:
# exact versions, every hash pinned, wheels only. $1 = the runtime's directory.
install_runtime_packages() {
    local dir="$1" want
    [[ -f "$RUNTIME_REQUIREMENTS" ]] || return 1
    want="$(sha256_file "$RUNTIME_REQUIREMENTS" | cut -d' ' -f1)"
    [[ "$(cat "$dir/.poketerm-packages" 2>/dev/null)" == "$want" ]] && return 0
    env ${POKETERM_DOWNLOAD_CACHE:+PIP_CACHE_DIR="$POKETERM_DOWNLOAD_CACHE/pip"} PIP_CONFIG_FILE=/dev/null \
        "$dir/bin/python3" -I -m pip install --quiet --disable-pip-version-check --no-input \
        --require-hashes --only-binary=:all: --no-deps -r "$RUNTIME_REQUIREMENTS" >>"$LOG_FILE" 2>&1 || return 1
    "$dir/bin/python3" -I -c 'from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec' >>"$LOG_FILE" 2>&1 || return 1
    printf '%s\n' "$want" > "$dir/.poketerm-packages"
}

python_runtime_skip() {
    # Soft failure: poketerm falls back to the system python3.
    printf '%s' "$C_CLEAR"
    warn "Couldn't set up poketerm's own Python ($1) - using the system python3 instead."
    echo "python runtime skipped: $1" >>"$LOG_FILE"
    CURRENT_STEP=""
    [[ -n "${py_tmp:-}" && -d "$py_tmp" ]] && rm -rf -- "$py_tmp"
    return 1
}

install_python_runtime() {
    # Runs in a `||` context (no set -e): every step is checked explicitly,
    # and the existing runtime is only replaced once the new one has passed.
    local target expected file actual cache old
    target="$(python_runtime_target)"
    [[ -n "$target" ]] || return 1
    expected="$(python_runtime_sha256 "$target")"
    [[ -n "$expected" ]] || return 1

    if [[ -x "$PYTHON_RUNTIME_DIR/bin/python3" && "$(cat "$PYTHON_RUNTIME_DIR/.poketerm-runtime" 2>/dev/null)" == "$(python_runtime_id)-$target" ]]; then
        # Same Python; bring its packages up to date if the pins changed.
        install_runtime_packages "$PYTHON_RUNTIME_DIR" \
            || warn "Couldn't update trading's packages - trading will use the openssl command instead."
        return 0
    fi
    command -v curl >/dev/null 2>&1 || return 1

    step "Installing poketerm's Python ($PYTHON_RUNTIME_VERSION, one-time download)"
    mkdir -p "$SHARE_DIR" 2>>"$LOG_FILE" || { python_runtime_skip "can't write to $SHARE_DIR"; return 1; }
    py_tmp="$(mktemp -d "$SHARE_DIR/.poketerm-python.XXXXXX")" || { python_runtime_skip "no temp dir"; return 1; }
    file="cpython-$PYTHON_RUNTIME_VERSION+$PYTHON_RUNTIME_RELEASE-$target-install_only_stripped.tar.gz"

    # POKETERM_DOWNLOAD_CACHE (tests): reuse a previous download. It is
    # verified against the pinned hash exactly like a fresh one.
    cache="${POKETERM_DOWNLOAD_CACHE:-}"
    if [[ -n "$cache" && -f "$cache/$file" ]] && [[ "$(sha256_file "$cache/$file" | cut -d' ' -f1)" == "$expected" ]]; then
        cp "$cache/$file" "$py_tmp/$file" 2>>"$LOG_FILE"
    fi
    if [[ ! -f "$py_tmp/$file" ]]; then
        curl -fsSL --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 10 --max-time 600 \
            -o "$py_tmp/$file" \
            "https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_RUNTIME_RELEASE/$file" \
            >>"$LOG_FILE" 2>&1 || { python_runtime_skip "download failed"; return 1; }
    fi
    actual="$(sha256_file "$py_tmp/$file" | cut -d' ' -f1)"
    if [[ "$actual" != "$expected" ]]; then
        echo "expected sha256 $expected, got $actual" >>"$LOG_FILE"
        python_runtime_skip "download did not match the pinned checksum"
        return 1
    fi
    if [[ -n "$cache" ]]; then
        mkdir -p "$cache" 2>/dev/null && cp "$py_tmp/$file" "$cache/$file.part" 2>/dev/null \
            && mv -f "$cache/$file.part" "$cache/$file" 2>/dev/null
    fi

    tar -xzf "$py_tmp/$file" -C "$py_tmp" >>"$LOG_FILE" 2>&1 && rm -f "$py_tmp/$file" \
        || { python_runtime_skip "could not unpack"; return 1; }
    # Must run here and have everything poketerm needs.
    "$py_tmp/python/bin/python3" -I -c 'import ssl, termios, fcntl, pty, json, hashlib, urllib.request, sys
sys.exit(0 if ssl.HAS_TLSv1_3 else 1)' >>"$LOG_FILE" 2>&1 \
        || { python_runtime_skip "it did not run on this system"; return 1; }
    printf '%s\n' "$(python_runtime_id)-$target" > "$py_tmp/python/.poketerm-runtime"
    install_runtime_packages "$py_tmp/python" \
        || warn "Couldn't install trading's packages - trading will use the openssl command instead."

    # Swap in: move the old one aside, move the new one in, then delete the old.
    old=""
    if [[ -e "$PYTHON_RUNTIME_DIR" ]]; then
        old="$py_tmp/old"
        mv "$PYTHON_RUNTIME_DIR" "$old" 2>>"$LOG_FILE" || { python_runtime_skip "could not replace the old one"; return 1; }
    fi
    if ! mv "$py_tmp/python" "$PYTHON_RUNTIME_DIR" 2>>"$LOG_FILE"; then
        [[ -n "$old" ]] && mv "$old" "$PYTHON_RUNTIME_DIR" 2>>"$LOG_FILE"
        python_runtime_skip "could not install"
        return 1
    fi
    rm -rf -- "$py_tmp"
    ok
}

# The Python poketerm will use: its own if installed, else the system one.
runtime_python() {
    if [[ -x "$PYTHON_RUNTIME_DIR/bin/python3" ]]; then echo "$PYTHON_RUNTIME_DIR/bin/python3"; else command -v python3; fi
}

# --- pokemon-go-colorscripts (fast Go build of pokemon-colorscripts) --------
# https://github.com/scottmckendry/pokemon-go-colorscripts (MIT)
# Pinned to a reviewed release. The SHA256 hashes below were recorded when
# v0.3.1 was reviewed and match the release's checksums.txt; a download that
# doesn't match is rejected. To upgrade: review the new release, then update
# the version AND all four hashes together.
GO_COLORSCRIPTS_VERSION="0.3.1"
go_colorscripts_sha256() {
    case "$1" in
        darwin_arm64) echo "d1b4d35d33590854aa01f0b2ea5da6bee7b1676fdfe4a4b1e997e433e8635dc0" ;;
        darwin_amd64) echo "f88871117cfbf967adeeb046793de161bee5588c6bd7eba6d2ceadcbb6d833fe" ;;
        linux_amd64)  echo "0b8934ed246144c56c23b90be95e6f5db7cfe3ed0b04b62ccfae79829f2fbeb4" ;;
        linux_arm64)  echo "1d860698a7dd0a4dfd7f7a22a4fb8954f6709cddede45fccacccc0dd3ce72642" ;;
    esac
}

go_colorscripts_skip() {
    # Soft failure: poketerm falls back to the Python pokemon-colorscripts.
    printf '%s' "$C_CLEAR"
    warn "Skipped the faster Go sprites ($1) - using the Python version instead."
    echo "go-colorscripts skipped: $1" >>"$LOG_FILE"
    CURRENT_STEP=""
    [[ -n "${go_tmp:-}" && -d "$go_tmp" ]] && rm -rf -- "$go_tmp"
    return 1
}

install_go_colorscripts() {
    # Every step is checked explicitly: this function runs in a `||` context
    # where `set -e` doesn't apply, and a failure must never abort the install.
    local os arch platform expected dest actual file
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    case "$(uname -m)" in
        x86_64|amd64)  arch=amd64 ;;
        arm64|aarch64) arch=arm64 ;;
        *) return 1 ;;
    esac
    platform="${os}_${arch}"
    expected="$(go_colorscripts_sha256 "$platform")"
    [[ -n "$expected" ]] || return 1

    dest="$SHARE_DIR/pokemon-go-colorscripts"
    if [[ -x "$dest/pokemon-go-colorscripts" && "$(cat "$dest/.version" 2>/dev/null)" == "$GO_COLORSCRIPTS_VERSION" ]]; then
        ln -sfn "$dest/pokemon-go-colorscripts" "$BIN_DIR/pokemon-go-colorscripts" 2>>"$LOG_FILE" || true
        return 0
    fi
    command -v curl >/dev/null 2>&1 || return 1

    step "Installing faster sprites (pokemon-go-colorscripts $GO_COLORSCRIPTS_VERSION)"
    go_tmp="$(mktemp -d)" || go_colorscripts_skip "no temp dir" || return 1
    file="pokemon-go-colorscripts_${GO_COLORSCRIPTS_VERSION}_${platform}.tar.gz"
    curl -fsSL --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 10 --max-time 300 \
        -o "$go_tmp/$file" \
        "https://github.com/scottmckendry/pokemon-go-colorscripts/releases/download/v${GO_COLORSCRIPTS_VERSION}/${file}" \
        >>"$LOG_FILE" 2>&1 || { go_colorscripts_skip "download failed"; return 1; }

    actual="$(sha256_file "$go_tmp/$file" | cut -d' ' -f1)"
    if [[ "$actual" != "$expected" ]]; then
        echo "expected sha256 $expected, got $actual" >>"$LOG_FILE"
        go_colorscripts_skip "download did not match the pinned checksum"
        return 1
    fi

    tar -xzf "$go_tmp/$file" -C "$go_tmp" pokemon-go-colorscripts LICENSE >>"$LOG_FILE" 2>&1 \
        || { go_colorscripts_skip "could not unpack"; return 1; }
    "$go_tmp/pokemon-go-colorscripts" --no-title -n pikachu >/dev/null 2>>"$LOG_FILE" \
        || { go_colorscripts_skip "binary did not run on this system"; return 1; }

    { mkdir -p "$dest" "$BIN_DIR" \
        && mv -f "$go_tmp/pokemon-go-colorscripts" "$dest/pokemon-go-colorscripts" \
        && chmod 755 "$dest/pokemon-go-colorscripts"; } >>"$LOG_FILE" 2>&1 \
        || { go_colorscripts_skip "could not install"; return 1; }
    cp "$go_tmp/LICENSE" "$dest/LICENSE" 2>/dev/null || true
    printf '%s\n' "$GO_COLORSCRIPTS_VERSION" > "$dest/.version"
    ln -sfn "$dest/pokemon-go-colorscripts" "$BIN_DIR/pokemon-go-colorscripts" 2>>"$LOG_FILE" || true
    rm -rf -- "$go_tmp"
    ok
}

check_python() {
    local py
    py="$(runtime_python)"
    if [[ -z "$py" ]]; then
        echo "No Python available: poketerm's own couldn't be installed and there is no python3" >>"$LOG_FILE"
        printf '%s' "$C_CLEAR"
        echo "${C_RED}poketerm needs Python.${C_RESET} Its own Python couldn't be downloaded and no python3 is installed."
        echo "Check your internet connection (or install python3) and run the installer again."
        false
    fi
    # Trading (press t in the Pokedex) needs TLS 1.3 and the openssl command.
    if ! "$py" -c 'import ssl, sys; sys.exit(0 if ssl.HAS_TLSv1_3 else 1)' >/dev/null 2>&1; then
        warn "this Python has no TLS 1.3, so trading won't be available (everything else works). Install a newer Python 3, e.g. brew install python"
    elif ! "$py" -c 'import cryptography' >/dev/null 2>&1 && ! command -v openssl >/dev/null 2>&1; then
        warn "trading needs poketerm's own Python or the openssl command, and neither is available (everything else works)"
    fi
}

install_pokemon_colorscripts() {
    # Fast path: the Go build. The Python one stays as the fallback.
    install_go_colorscripts && return 0

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
    install_python_runtime || true
    check_python
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
    [[ -n "$INSTALL_ROOT" && -d "$SHARE_DIR/pokemon-go-colorscripts" ]] && rm -rf -- "$SHARE_DIR/pokemon-go-colorscripts"
    [[ -n "$INSTALL_ROOT" && -d "$SHARE_DIR/poketerm-python" ]] && rm -rf -- "$SHARE_DIR/poketerm-python"
    rm -f "$BIN_DIR/poketerm" "$BIN_DIR/pokedex" "$BIN_DIR/pokemon-colorscripts" "$BIN_DIR/pokemon-go-colorscripts"

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
