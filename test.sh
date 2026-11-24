#!/usr/bin/env bash
# End-to-end tests for poketerm. Installs into a throwaway HOME (your real
# install, ~/.zshrc and progress are never touched) and checks catching, XP,
# sprite sizes, settings and updates.
#
#   ./test.sh          run everything
#   ./test.sh -v       also print each poketerm run's output on failure
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE=false
[[ "${1:-}" == "-v" ]] && VERBOSE=true

SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/poketerm-test.XXXXXX")"
cleanup() { [[ -n "$SANDBOX" && -d "$SANDBOX" ]] && chmod -R u+w "$SANDBOX" 2>/dev/null; rm -rf -- "$SANDBOX"; }
trap cleanup EXIT

T_HOME="$SANDBOX/home"
APP="$T_HOME/.local/share/poketerm"
mkdir -p "$T_HOME"

PASS=0
FAIL=0
LAST_OUT="$SANDBOX/last_output"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'; else G=""; R=""; D=""; N=""; fi

pass() { PASS=$((PASS + 1)); echo "  ${G}✔${N} $1"; }
fail() {
    FAIL=$((FAIL + 1)); echo "  ${R}✖ $1${N}"
    [[ -n "${2:-}" ]] && echo "      ${D}$2${N}"
    if [[ "$VERBOSE" == true && -s "$LAST_OUT" ]]; then sed 's/^/      | /' "$LAST_OUT" | head -30; fi
}
check() { # check "description" <command...>
    local desc="$1"; shift
    if "$@"; then pass "$desc"; else fail "$desc"; fi
}
eq() { # eq "description" actual expected
    if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1" "expected '$3', got '$2'"; fi
}
section() { echo ""; echo "$1"; }

# Run poketerm in the sandbox like a new terminal would. Extra env via "VAR=value" args.
poketerm_run() {
    env -i HOME="$T_HOME" USER=tester PATH="$T_HOME/.local/bin:$PATH" TERM=xterm-256color \
        LANG="${LANG:-en_US.UTF-8}" POKETERM_NO_UPDATE_CHECK=1 "$@" \
        zsh "$APP/poketerm" > "$LAST_OUT" 2>&1 < /dev/null
}
poketerm_cmd() { # poketerm_cmd [VAR=value...] -- args...
    local -a envs=()
    while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
    shift
    env -i HOME="$T_HOME" USER=tester PATH="$T_HOME/.local/bin:$PATH" POKETERM_NO_UPDATE_CHECK=1 \
        ${envs[@]+"${envs[@]}"} zsh "$APP/poketerm" "$@" > "$LAST_OUT" 2>&1 < /dev/null
}
installer() {
    HOME="$T_HOME" POKETERM_REAL_HOME="$T_HOME" POKETERM_ZSHRC="$T_HOME/.zshrc" \
        bash "$REPO_ROOT/install.sh" "$@" > "$SANDBOX/install.log" 2>&1
}

total_catches() { awk '{ s += $NF } END { print s + 0 }' "$APP/pokedex.txt"; }
trainer_field() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval('d' + sys.argv[2]))" "$APP/trainer.json" "$1"; }
hash_ok()       { (cd / && shasum -a 256 -c "$APP/pokedex.txt.sha256" >/dev/null 2>&1); }
# The Pokemon caught by the last run (diff of the pokedex before/after).
caught_name()   { diff <(echo "$1") <(cat "$APP/pokedex.txt") | sed -n 's/^> //p' | sed -E 's/ [0-9]+$//; s/ \(shiny\)$//' | head -n 1; }
colorscripts()  {
    local go="$T_HOME/.local/share/pokemon-go-colorscripts/pokemon-go-colorscripts"
    if [[ -x "$go" ]]; then echo "$go"; else command -v pokemon-go-colorscripts || command -v pokemon-colorscripts; fi
}
plain()         { perl -CSD -pe 's/\e\[[0-9;?]*[a-zA-Z]//g; s/\s+$//; $_ .= "\n"'; }

echo "poketerm tests (sandbox: $SANDBOX)"

# -----------------------------------------------------------------------------
section "Install"
if installer; then pass "fresh install succeeds"; else fail "fresh install succeeds" "$(tail -5 "$SANDBOX/install.log")"; exit 1; fi
check "commands linked"            test -L "$T_HOME/.local/bin/poketerm" -a -L "$T_HOME/.local/bin/pokedex"
check "zshrc hook added"           grep -qxF "# >>> POKETERM SCRIPT BEGIN >>>" "$T_HOME/.zshrc"
check "trainer profile seeded"     test -s "$APP/trainer.json"
eq    "VERSION written"            "$(cat "$APP/VERSION")" "$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"

# -----------------------------------------------------------------------------
section "Catching and XP"
poketerm_run
eq    "first terminal exits cleanly"     "$?" "0"
eq    "one Pokemon caught"               "$(total_catches)" "1"
check "pokedex checksum still valid"     hash_ok
xp="$(trainer_field "['total_xp']")"
check "first catch gives 10 or 150 XP ($xp)" test "$xp" = 10 -o "$xp" = 150
check "first-catch achievement unlocked" grep -q "Just getting started" "$APP/trainer.json"
check "username and underline shown"     grep -q -- "^.*tester" "$LAST_OUT"

section "Sprites are shown at their original size"
dex_before="$(cat "$APP/pokedex.txt")"
poketerm_run
mon="$(caught_name "$dex_before")"
cs="$(colorscripts)"
if [[ -n "$mon" && -n "$cs" ]]; then
    "$cs" --no-title -n "$mon" | plain | sed -e :a -e '/^$/{$d;N;ba' -e '}' > "$SANDBOX/expected_sprite"
    grep -q "(shiny)" <(tail -n 1 "$APP/pokedex.txt") && "$cs" --no-title -n "$mon" -s | plain > "$SANDBOX/expected_sprite"
    width="$(perl -CSD -ne 'chomp; $w = length if length > $w; END { print $w + 0 }' "$SANDBOX/expected_sprite")"
    rows="$(wc -l < "$SANDBOX/expected_sprite" | tr -d ' ')"
    plain < "$LAST_OUT" | head -n "$rows" | perl -CSD -ne "chomp; print substr(\$_, 0, $width) =~ s/\s+\$//r, \"\n\"" > "$SANDBOX/shown_sprite"
    sed -i.bak -e 's/[[:space:]]*$//' "$SANDBOX/expected_sprite"
    check "sprite for $mon is exactly the colorscripts sprite (${width}x${rows}, not resized)" \
        cmp -s "$SANDBOX/expected_sprite" "$SANDBOX/shown_sprite"
    check "Pokemon name shown under the sprite" grep -q "^$mon" <(plain < "$LAST_OUT")
else
    fail "could determine the caught Pokemon and colorscripts"
fi
check "no resizing leftovers installed" test ! -e "$APP/sprite.py" -a ! -d "$APP/cache/sprites_fit"

# -----------------------------------------------------------------------------
section "Many terminals opening at once"
shiny_catches() { awk '/\(shiny\)/ { s += $NF } END { print s + 0 }' "$APP/pokedex.txt"; }
start_xp="$(trainer_field "['total_xp']")"; start_catches="$(total_catches)"; start_shiny="$(shiny_catches)"
for i in 1 2 3 4 5 6 7 8; do
    ( poketerm_run; echo "$?" > "$SANDBOX/parallel.$i" ) &
done
wait
caught=$(( $(total_catches) - start_catches ))
shiny=$(( $(shiny_catches) - start_shiny ))
gained=$(( $(trainer_field "['total_xp']") - start_xp ))
eq    "8 terminals at once: every catch recorded"      "$caught" "8"
eq    "8 terminals at once: XP exactly matches catches" "$gained" "$(( (caught - shiny) * 10 + shiny * 150 ))"
check "8 terminals at once: none reported a tampered pokedex" \
    test "$(cat "$SANDBOX"/parallel.* | sort -u)" = "0"
check "pokedex checksum valid after parallel starts" hash_ok

# -----------------------------------------------------------------------------
section "Generations"
chmod 644 "$APP/pokedex.txt"; : > "$APP/pokedex.txt"; chmod 444 "$APP/pokedex.txt"
shasum -a 256 "$APP/pokedex.txt" > "$APP/pokedex.txt.sha256"
for _ in 1 2 3 4 5 6; do poketerm_cmd -- --gen 3 >/dev/null; done
others="$(awk '{ n = $0; sub(/ [0-9]+$/, "", n); sub(/ \(shiny\)$/, "", n); print n }' "$APP/pokedex.txt" \
    | grep -vxFf "$REPO_ROOT/files/gen_files/gen3_list.txt" | wc -l | tr -d ' ')"
eq "--gen 3 only catches Gen 3 Pokemon" "$others" "0"

# -----------------------------------------------------------------------------
section "Updating keeps progress and settings"
xp_before="$(trainer_field "['total_xp']")"; dex_before="$(cat "$APP/pokedex.txt")"
cp "$T_HOME/.zshrc" "$SANDBOX/zshrc.before"
if installer --update; then pass "update succeeds"; else fail "update succeeds" "$(tail -5 "$SANDBOX/install.log")"; fi
eq    "XP kept"                        "$(trainer_field "['total_xp']")" "$xp_before"
eq    "pokedex kept"                   "$(cat "$APP/pokedex.txt")" "$dex_before"
check "zshrc unchanged by update"      cmp -s "$T_HOME/.zshrc" "$SANDBOX/zshrc.before"
check "progress backed up before update" test -f "$(ls -d "$APP"/backups/*/ | tail -n 1)trainer.json"
poketerm_run
eq    "first terminal after update works" "$?" "0"

# -----------------------------------------------------------------------------
echo ""
if (( FAIL == 0 )); then
    echo "${G}All $PASS checks passed.${N}"
else
    echo "${R}$FAIL of $((PASS + FAIL)) checks failed.${N} Re-run with -v for output."
fi
exit $(( FAIL > 0 ))
