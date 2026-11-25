#!/usr/bin/env bash
# Poketerm self-updater. Run via:  poketerm --update [VERSION] [--beta] [--check] [--force]
set -euo pipefail

REPO="${POKETERM_REPO:-chris-wood-mo/poketerm}"
APP_DIR="${POKETERM_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
INSTALL_ROOT="$(cd "$APP_DIR/../.." && pwd)"
CHECK_FILE="$APP_DIR/.update_check"
API="https://api.github.com/repos/$REPO"

installed="$(tr -d '[:space:]' < "$APP_DIR/VERSION" 2>/dev/null || true)"
installed="${installed:-0.0.0}"

conf_get() {
    [[ -f "$APP_DIR/install.conf" ]] || return 0
    sed -n "s/^$1=//p" "$APP_DIR/install.conf" | head -n 1
}

usage() {
    cat <<EOF
Update poketerm (installed: $installed)

Usage: poketerm --update [VERSION] [OPTIONS]

  (no arguments)     Update to the latest stable release
  VERSION            Install a specific release, e.g. 0.0.7 (also used to roll back)
  --beta             Include pre-releases (for testing new versions early)
  --check            Only check whether an update is available
  --force            Reinstall even if already up to date, or allow a downgrade
  --from PATH        Install from a local poketerm checkout instead of GitHub
  -h, --help         Show this help

Your Pokedex and trainer progress are always kept, and backed up before
every update to: $APP_DIR/backups
EOF
}

# --- version comparison: returns 0 if $1 > $2 (semver, ignores a leading "v"
#     and "+build" metadata; a pre-release like 1.0.0-beta.1 sorts before 1.0.0)
version_gt() {
    local a="${1#v}" b="${2#v}"
    a="${a%%+*}"; b="${b%%+*}"
    local a_base="${a%%-*}" b_base="${b%%-*}" a_pre="" b_pre=""
    [[ "$a" == *-* ]] && a_pre="${a#*-}"
    [[ "$b" == *-* ]] && b_pre="${b#*-}"

    local IFS=.
    local -a av=($a_base) bv=($b_base)
    local i x y
    for i in 0 1 2; do
        x="${av[$i]:-0}"; y="${bv[$i]:-0}"
        [[ "$x" =~ ^[0-9]+$ ]] || x=0
        [[ "$y" =~ ^[0-9]+$ ]] || y=0
        (( 10#$x > 10#$y )) && return 0
        (( 10#$x < 10#$y )) && return 1
    done

    [[ -z "$a_pre" && -n "$b_pre" ]] && return 0
    [[ -n "$a_pre" && -z "$b_pre" ]] && return 1
    [[ "$a_pre" > "$b_pre" ]]
}

fetch() {
    curl -fsSL --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 5 --max-time "${2:-15}" \
        -H "Accept: application/vnd.github+json" -H "User-Agent: poketerm-updater" "$1"
}

latest_tag() {
    local json
    if [[ "$1" == beta ]]; then
        json="$(fetch "$API/releases?per_page=1")"
    else
        json="$(fetch "$API/releases/latest")"
    fi
    printf '%s\n' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

# ------------------------------------------------------------------
channel=stable
target=""
from=""
force=false
check_only=false
quiet=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --beta)   channel=beta; shift ;;
        --force)  force=true; shift ;;
        --check)  check_only=true; shift ;;
        --quiet)  quiet=true; shift ;;
        --from)
            [[ $# -ge 2 ]] || { echo "Error: --from needs a path"; exit 1; }
            from="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "Unknown option: $1"; usage; exit 1 ;;
        *)
            [[ "$1" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || { echo "Error: '$1' is not a valid version (e.g. 0.0.7)"; exit 1; }
            target="$1"; shift ;;
    esac
done

# --- background/explicit availability check --------------------------------
if [[ "$check_only" == true ]]; then
    latest="$(latest_tag "$channel" 2>/dev/null || true)"
    if [[ -z "$latest" ]]; then
        [[ "$quiet" == true ]] || echo "Could not reach GitHub to check for updates."
        exit 1
    fi
    newer=0
    version_gt "$latest" "$installed" && newer=1
    printf '%s %s %s\n' "$(date +%s)" "$latest" "$newer" > "$CHECK_FILE.tmp" && mv -f "$CHECK_FILE.tmp" "$CHECK_FILE"
    if [[ "$quiet" != true ]]; then
        if (( newer )); then
            echo "Update available: $installed -> $latest  (run: poketerm --update)"
        else
            echo "Poketerm $installed is up to date."
        fi
    fi
    exit 0
fi

# --- installing from a local checkout ----------------------------------------
export POKETERM_INSTALL_ROOT="$INSTALL_ROOT"
bin_dir="$(conf_get BIN_DIR)"; [[ -n "$bin_dir" ]] && export POKETERM_BIN_DIR="$bin_dir"
zshrc="$(conf_get ZSHRC)";     [[ -n "$zshrc" ]] && export POKETERM_ZSHRC="$zshrc"

if [[ -n "$from" ]]; then
    from="$(cd "$from" 2>/dev/null && pwd)" || { echo "Error: $from does not exist"; exit 1; }
    [[ -f "$from/install.sh" && -d "$from/files" ]] || { echo "Error: $from is not a poketerm checkout"; exit 1; }
    exec bash "$from/install.sh" --update
fi

if [[ "$(conf_get MODE)" == dev && "$force" != true ]]; then
    echo "This is a dev install linked to $(conf_get SOURCE)."
    echo "Update it with git there, or run 'poketerm --update --force' to switch to the release build."
    exit 1
fi

command -v curl >/dev/null 2>&1 || { echo "Error: curl is required to update."; exit 1; }
command -v tar  >/dev/null 2>&1 || { echo "Error: tar is required to update."; exit 1; }

# --- pick the version ---------------------------------------------------------
if [[ -z "$target" ]]; then
    echo "Checking for updates..."
    target="$(latest_tag "$channel" || true)"
    if [[ -z "$target" ]]; then
        echo "Could not reach GitHub to check for updates. Check your connection and try again."
        exit 1
    fi
    if ! version_gt "$target" "$installed" && [[ "$force" != true ]]; then
        echo "Poketerm $installed is already up to date."
        printf '%s %s 0\n' "$(date +%s)" "$target" > "$CHECK_FILE"
        exit 0
    fi
else
    target="${target#v}"
    if ! version_gt "$target" "$installed" && [[ "$force" != true ]]; then
        if [[ "${target}" == "${installed%%+*}" ]]; then
            echo "Poketerm $target is already installed (use --force to reinstall)."
        else
            echo "$target is older than the installed $installed. Use --force to downgrade."
        fi
        exit 0
    fi
fi

# --- download and install -------------------------------------------------------
tmp="$(mktemp -d "${TMPDIR:-/tmp}/poketerm-update.XXXXXX")"
cleanup() { [[ -n "${tmp:-}" && -d "$tmp" ]] && rm -rf -- "$tmp"; }
trap cleanup EXIT

echo "Downloading poketerm $target..."
if ! curl -fsSL --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 5 --max-time 300 \
        -o "$tmp/poketerm.tar.gz" "https://github.com/$REPO/archive/refs/tags/$target.tar.gz"; then
    echo "Could not download version $target. Check the version exists: https://github.com/$REPO/releases"
    exit 1
fi

mkdir "$tmp/src"
tar -xzf "$tmp/poketerm.tar.gz" -C "$tmp/src" --strip-components 1
if [[ ! -f "$tmp/src/install.sh" || ! -d "$tmp/src/files" ]]; then
    echo "The downloaded release doesn't look like poketerm - aborting. Nothing was changed."
    exit 1
fi

bash "$tmp/src/install.sh" --update
