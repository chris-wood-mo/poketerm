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
RUNTIME="$T_HOME/.local/share/poketerm-python"
mkdir -p "$T_HOME"
# Every sandbox install reuses one download of poketerm's Python. The installer
# checks a cached copy against the pinned hash exactly like a fresh download.
export POKETERM_DOWNLOAD_CACHE="${POKETERM_DOWNLOAD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/poketerm-test-downloads}"

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
trainer_field() {  # retried, and any error shown, so a flaky helper can't pass as a poketerm bug
    local out i
    for i in 1 2 3; do
        out="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval('d' + sys.argv[2]))" "$APP/trainer.json" "$1" 2>&1)" \
            && { echo "$out"; return 0; }
        echo "trainer_field attempt $i failed: $out" >&2
        sleep 0.2
    done
    return 1
}
hash_ok()       { (cd / && shasum -a 256 -c "$APP/pokedex.txt.sha256" >/dev/null 2>&1); }
# The Pokemon caught by the last run (the pokedex line that is new or changed).
caught_name()   { grep -vxF -f <(printf '%s\n' "$1") "$APP/pokedex.txt" | sed -E 's/ [0-9]+$//; s/ \(shiny\)$//' | head -n 1; }
same_file()     { [[ "$(cat "$1")" == "$(cat "$2")" ]]; }   # no cmp/diff on minimal systems
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
installer --dev
check "--dev is gone and says what to use instead" grep -q "poketerm --update --from" "$SANDBOX/install.log"
check "...and the install is a plain copy (no links to the checkout)" bash -c '[[ -z "$(find "$1" -maxdepth 1 -type l)" ]]' _ "$APP"

section "Catching and XP"
poketerm_run
eq    "first terminal exits cleanly"     "$?" "0"
eq    "one Pokemon caught"               "$(total_catches)" "1"
check "pokedex checksum still valid"     hash_ok
xp="$(trainer_field "['total_xp']")"
check "first catch gives 10 or 150 XP ($xp)" test "$xp" = 10 -o "$xp" = 150
check "first-catch achievement unlocked" grep -q "Just getting started" "$APP/trainer.json"
check "username and underline shown"     grep -q -- "^.*tester" "$LAST_OUT"
caught_one="$(sed -E 's/ [0-9]+$//; s/ \(shiny\)$//' "$APP/pokedex.txt" | head -n 1)"
check "the catch is remembered: its name, time, nature, trainer and catch number" python3 - "$APP/pokemon.log" "$caught_one" <<'PY'
import json, sys, re
events = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
caught = [e["pokemon"] for e in events if e["e"] == "caught"]
p = caught[0] if len(caught) == 1 else {}
sys.exit(0 if p.get("name") == sys.argv[2] and re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d", p.get("caught_at", ""))
         and p.get("nature") and p.get("catch_no") == 1 and "original_trainer" in p else 1)
PY

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
        same_file "$SANDBOX/expected_sprite" "$SANDBOX/shown_sprite"
    check "Pokemon name shown under the sprite" grep -q "^$mon" <(plain < "$LAST_OUT")
else
    fail "could determine the caught Pokemon and colorscripts"
fi
check "no resizing leftovers installed" test ! -e "$APP/sprite.py" -a ! -d "$APP/cache/sprites_fit"
check "the old sprite cache isn't installed (colorscripts draws them)" test ! -e "$APP/cache/sprite_data"
check "...but the Pokemon data cache is (entries work offline)" test -f "$APP/cache/api_data/pikachu.json"

# -----------------------------------------------------------------------------
section "Many terminals opening at once"
shiny_catches() { awk '/\(shiny\)/ { s += $NF } END { print s + 0 }' "$APP/pokedex.txt"; }
start_xp="$(trainer_field "['total_xp']")"; start_catches="$(total_catches)"; start_shiny="$(shiny_catches)"
for i in $(seq 1 30); do
    ( poketerm_run; echo "$?" > "$SANDBOX/parallel.$i" ) &
done
wait
caught=$(( $(total_catches) - start_catches ))
shiny=$(( $(shiny_catches) - start_shiny ))
gained=$(( $(trainer_field "['total_xp']") - start_xp ))
eq    "30 terminals at once: every catch recorded"      "$caught" "30"
eq    "30 terminals at once: XP exactly matches catches" "$gained" "$(( (caught - shiny) * 10 + shiny * 150 ))"
check "30 terminals at once: all succeeded, none reported tampering" \
    test "$(cat "$SANDBOX"/parallel.* | sort -u)" = "0"
check "pokedex checksum valid after parallel starts" hash_ok

# -----------------------------------------------------------------------------
section "Recovery and edge cases"
reset_dex() { chmod 644 "$APP/pokedex.txt"; printf '%s\n' "$@" > "$APP/pokedex.txt"; chmod 444 "$APP/pokedex.txt"; shasum -a 256 "$APP/pokedex.txt" > "$APP/pokedex.txt.sha256"; }
reset_dex "pikachu 3" "pikachu (shiny) 1" "raichu 2" "mew 1" "mewtwo 4" "mr-mime 1" "mime-jr 2"
poketerm_cmd -- --remove chu
check "--remove needs an exact name ('chu' removes nothing)" grep -q "^pikachu 3" "$APP/pokedex.txt"
poketerm_cmd -- --remove mime
check "--remove 'mime' keeps mr-mime and mime-jr" grep -q "^mr-mime 1" "$APP/pokedex.txt"
poketerm_cmd -- --remove MEW
check "--remove is case-insensitive and exact (mew gone, mewtwo kept)" test "$(grep -c '^mew ' "$APP/pokedex.txt")" = 0 -a "$(grep -c '^mewtwo 4' "$APP/pokedex.txt")" = 1
poketerm_cmd -- --remove pikachu
check "--remove removes normal and shiny entries" test "$(grep -c '^pikachu' "$APP/pokedex.txt")" = 0
check "checksum valid after --remove"  hash_ok

chmod 644 "$APP/pokedex.txt"; echo "snorlax 50" >> "$APP/pokedex.txt"; chmod 444 "$APP/pokedex.txt"
before="$(cat "$APP/pokedex.txt")"
poketerm_run
eq    "hand-edited pokedex is refused"  "$?" "1"
check "refusal explains how to fix it"  grep -q "modified outside poketerm" "$LAST_OUT"
eq    "refused catch leaves pokedex untouched" "$(cat "$APP/pokedex.txt")" "$before"
poketerm_cmd -- --remove snorlax
eq    "--remove works on a hand-edited pokedex (the documented fix)" "$?" "0"
poketerm_run
eq    "catching works again after --remove" "$?" "0"

rm -f "$APP/pokedex.txt"
poketerm_run
eq    "deleted pokedex.txt starts fresh instead of locking the user out" "$?" "0"
eq    "fresh pokedex has the new catch" "$(total_catches)" "1"

cp "$APP/trainer.json" "$SANDBOX/trainer.good"
echo '{"name": "Ash", "total_xp": 12' > "$APP/trainer.json"
poketerm_run
eq    "corrupt trainer.json doesn't stop catching" "$?" "0"
check "corrupt trainer.json is kept for recovery" bash -c 'compgen -G "$1" >/dev/null' _ "$APP/trainer.json.corrupt-*"
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$APP/trainer.json" && pass "trainer.json is valid again" || fail "trainer.json still invalid"
rm -f "$APP"/trainer.json.corrupt-*; cp "$SANDBOX/trainer.good" "$APP/trainer.json"

before="$(total_catches)"
poketerm_cmd -- --not-a-real-option
eq    "unknown option is rejected"       "$?" "1"
eq    "unknown option doesn't catch"     "$(total_catches)" "$before"
check "unknown option shows the help"    grep -q "Usage:" "$LAST_OUT"

for locale in C en_US.UTF-8; do
    poketerm_run LANG="$locale" LC_ALL="$locale"
    cols="$(perl -CSD -ne 's/\e\[[0-9;?]*[a-zA-Z]//g; print index($_, $1), "\n" if /(tester|OS:|Host:|Kernel:|Shell:)/' "$LAST_OUT" | sort -u | wc -l | tr -d ' ')"
    check "LANG=$locale: username and system details line up in one column" test "$cols" -eq 1
done

# Keys pressed while a pokedex screen is still drawing must not be lost.
typeahead="$(cd "$APP" && python3 - <<'EOF'
import os, pty, sys, time, select
pid, fd = pty.fork()
if pid == 0:
    import readchar
    print("drawing", flush=True); time.sleep(0.5)
    print("GOT=" + repr(readchar.readkey()), flush=True); os._exit(0)
out, sent, end = b"", False, time.time() + 4
while time.time() < end and b"GOT=" not in out:
    if select.select([fd], [], [], 0.1)[0]:
        try: out += os.read(fd, 1024)
        except OSError: break
    if b"drawing" in out and not sent:
        time.sleep(0.1); os.write(fd, b"b"); sent = True
if b"GOT=" not in out: os.kill(pid, 9)
print("ok" if b"GOT='b'" in out else ("unsupported" if b"Function not implemented" in out else "lost"))
EOF
)"
if [[ "$typeahead" == unsupported ]]; then
    echo "  - skipped: pokedex key handling (this environment has no terminal support, e.g. CPU emulation)"
else
    eq    "pokedex keeps keys pressed while a screen is drawing" "$typeahead" "ok"
fi

# -----------------------------------------------------------------------------
section "System details (fastfetch, or neofetch)"
details() { plain < "$LAST_OUT" | grep -oE '(OS|Host|Kernel|Uptime|Packages|Shell|Terminal): .*' ; }
if command -v fastfetch >/dev/null 2>&1 || command -v neofetch >/dev/null 2>&1; then
    poketerm_run
    if details | grep -q '^OS: ' && details | grep -q '^Kernel: '; then
        pass "system details shown beside the Pokemon"
    else
        fail "system details shown beside the Pokemon" "$(plain < "$LAST_OUT" | tail -12 | tr '\n' '|')"
    fi
else
    echo "  - skipped: neither fastfetch nor neofetch is installed"
fi

# With fastfetch, each line must be exactly what neofetch printed, so
# terminals look the same after switching. Compared directly (uptime can
# tick over a minute between the two, so allow one retry).
if command -v fastfetch >/dev/null 2>&1 && command -v neofetch >/dev/null 2>&1; then
    sed -n '/^print_info() {/,/^}/p' "$APP/poketerm" > "$SANDBOX/neofetch.cfg"
    fetch_compare() {
        POKETERM_RUNNING=1 neofetch --config "$SANDBOX/neofetch.cfg" --off </dev/null 2>/dev/null \
            | perl -pe 's/\e\[\?\d+[hl]//g' | grep -v '^[[:space:]]*$' > "$SANDBOX/neo.out"
        fastfetch --config none --format json -s OS:Host:Kernel:Uptime:Packages:Shell:Terminal 2>/dev/null </dev/null \
            | python3 "$APP/sysinfo.py" > "$SANDBOX/ff.out"
        same_file "$SANDBOX/neo.out" "$SANDBOX/ff.out"
    }
    if fetch_compare || fetch_compare; then
        pass "fastfetch details are byte-for-byte what neofetch showed"
    else
        fail "fastfetch details are byte-for-byte what neofetch showed" \
            "neofetch: $(plain < "$SANDBOX/neo.out" | tr '\n' '|')  fastfetch: $(plain < "$SANDBOX/ff.out" | tr '\n' '|')"
    fi
else
    echo "  - skipped: byte-for-byte comparison needs both fastfetch and neofetch installed"
fi

# A broken or odd fastfetch must never stop a catch or print an error.
stub="$T_HOME/.local/bin/fastfetch"
for kind in "prints garbage" "fails" "returns odd values"; do
    case "$kind" in
        "prints garbage")     body='echo "not json"' ;;
        "fails")              body='exit 3' ;;
        "returns odd values") body='echo "[{\"type\":\"OS\",\"result\":{\"prettyName\":7}},{\"type\":\"Uptime\",\"result\":{\"uptime\":\"x\"}}]"' ;;
    esac
    printf '#!/bin/sh\n%s\n' "$body" > "$stub"; chmod +x "$stub"
    before="$(total_catches)"
    poketerm_run
    rc=$?
    if [[ $rc == 0 && "$(total_catches)" == $((before + 1)) ]] && ! grep -qi "traceback\|error" "$LAST_OUT"; then
        pass "fastfetch that $kind: Pokemon still caught, no errors shown"
    else
        fail "fastfetch that $kind: Pokemon still caught, no errors shown" "exit $rc; $(plain < "$LAST_OUT" | tail -5 | tr '\n' '|')"
    fi
done
rm -f "$stub"

# -----------------------------------------------------------------------------
section "Trading"
# Real trades between two sandboxed trainers (separate processes and homes),
# including crashes at every step and the trade screens themselves.
trade_results() {
    while IFS= read -r line; do
        case "$line" in
            "PASS "*) pass "${line#PASS }" ;;
            "FAIL "*) desc="${line#FAIL }"; fail "${desc%% :: *}" "$([[ "$desc" == *" :: "* ]] && echo "${desc#* :: }" | cut -c1-400)" ;;
            "SKIP "*) echo "  - skipped: ${line#SKIP }" ;;
        esac
    done
}
# (process substitution, not a pipe, so pass/fail counts reach the summary)
unit_python="$RUNTIME/bin/python3"; [[ -x "$unit_python" ]] || unit_python=python3
trade_results < <("$unit_python" -s "$REPO_ROOT/test_trade.py" unit 2>&1)
trade_results < <(python3 "$REPO_ROOT/test_trade.py" "$SANDBOX/trading" 2>&1)

# -----------------------------------------------------------------------------
section "Generations"
chmod 644 "$APP/pokedex.txt"; : > "$APP/pokedex.txt"; chmod 444 "$APP/pokedex.txt"
shasum -a 256 "$APP/pokedex.txt" > "$APP/pokedex.txt.sha256"
for _ in 1 2 3 4 5 6; do poketerm_cmd -- --gen 3 >/dev/null; done
others="$(awk '{ n = $0; sub(/ [0-9]+$/, "", n); sub(/ \(shiny\)$/, "", n); print n }' "$APP/pokedex.txt" \
    | grep -vxFf "$REPO_ROOT/files/gen_files/gen3_list.txt" | wc -l | tr -d ' ')"
eq "--gen 3 only catches Gen 3 Pokemon" "$others" "0"

# -----------------------------------------------------------------------------
section "Settings (Pokedex: press s)"
gen_of() { local g; for g in 1 2 3 4 5 6 7 8; do grep -qx "$1" "$APP/gen_files/gen${g}_list.txt" && { echo "$g"; return; }; done; }
printf 'generations=2,7\nsystem_details=off\n' > "$APP/settings.conf"
seen=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    dex_before="$(cat "$APP/pokedex.txt")"; poketerm_run
    seen="$seen $(gen_of "$(caught_name "$dex_before")")"
done
eq    "only the chosen generations are caught (2 and 7)" "$(tr ' ' '\n' <<< "$seen" | grep -v '^$' | sort -u | tr '\n' ' ')" "2 7 "
check "system details off: no username or details beside the catch" bash -c '! grep -q "tester\|OS:" "$1"' _ "$LAST_OUT"
dex_before="$(cat "$APP/pokedex.txt")"; poketerm_cmd -- --gen 5 >/dev/null
eq    "a --gen typed by hand still wins for that run" "$(gen_of "$(caught_name "$dex_before")")" "5"
printf 'generations=9,x\nsystem_details=maybe\n' > "$APP/settings.conf"
poketerm_run
eq    "nonsense in the settings file falls back to the defaults" "$?" "0"
rm -f "$APP/settings.conf"
g_home="$SANDBOX/gen-home"; mkdir -p "$g_home"
# Reuse the Python already installed for the main test home (no second download).
[[ -d "$RUNTIME" ]] && { mkdir -p "$g_home/.local/share"; ln -s "$RUNTIME" "$g_home/.local/share/poketerm-python"; }
HOME="$g_home" POKETERM_REAL_HOME="$g_home" POKETERM_ZSHRC="$g_home/.zshrc" bash "$REPO_ROOT/install.sh" --gen 2-4 > "$SANDBOX/gen-install.log" 2>&1
eq    "install --gen 2-4 saves the generations as a setting" "$(grep '^generations=' "$g_home/.local/share/poketerm/settings.conf")" "generations=2,3,4"
check "...and the zshrc hook is plain 'poketerm' (no --gen)" bash -c 'grep -qx "  poketerm" "$1" && ! grep -q -- "--gen" "$1"' _ "$g_home/.zshrc"
sed -i.bak 's/^  poketerm$/  poketerm --gen 6/' "$g_home/.zshrc"; rm -f "$g_home/.local/share/poketerm/settings.conf"
HOME="$g_home" POKETERM_REAL_HOME="$g_home" POKETERM_ZSHRC="$g_home/.zshrc" bash "$REPO_ROOT/install.sh" --update > "$SANDBOX/gen-install.log" 2>&1
check "an old hook's --gen moves into the settings on update" grep -qx "generations=6" "$g_home/.local/share/poketerm/settings.conf"
check "...and the hook loses its --gen" bash -c '! grep -q -- "--gen" "$1"' _ "$g_home/.zshrc"
chmod -R u+w "$g_home" 2>/dev/null; rm -rf -- "$g_home"

# -----------------------------------------------------------------------------
section "Moving to a new computer (--export / --import)"
export POKETERM_PASSPHRASE="test passphrase 1"
PYTHONPATH="$APP" HOME="$T_HOME" "$unit_python" -s -c 'import trade_store; trade_store.load_identity()' 2>/dev/null
printf 'animations=off\n' > "$APP/settings.conf"
x_dir="$SANDBOX/moving"; mkdir -p "$x_dir"; x_file="$x_dir/me.poketerm"
poketerm_cmd POKETERM_PASSPHRASE="$POKETERM_PASSPHRASE" -- --export "$x_file"
eq    "export succeeds" "$?" "0"
check "...the file is private to you (0600)" bash -c '[[ "$(stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1")" == 600 ]]' _ "$x_file"
check "...and nothing in it is readable without the passphrase" bash -c '! grep -qa "pikachu\|PRIVATE KEY\|tester" "$1"' _ "$x_file"
poketerm_cmd POKETERM_PASSPHRASE="$POKETERM_PASSPHRASE" -- --export "$x_file"
check "an export never overwrites an existing file" bash -c '[[ $1 != 0 ]] && grep -q "already exists" "$2"' _ "$?" "$LAST_OUT"
n_home="$SANDBOX/new-computer"; mkdir -p "$n_home"
[[ -d "$RUNTIME" ]] && { mkdir -p "$n_home/.local/share"; ln -s "$RUNTIME" "$n_home/.local/share/poketerm-python"; }
HOME="$n_home" POKETERM_REAL_HOME="$n_home" POKETERM_ZSHRC="$n_home/.zshrc" bash "$REPO_ROOT/install.sh" > "$SANDBOX/new-install.log" 2>&1
N_APP="$n_home/.local/share/poketerm"
n_run() {  # n_run [VAR=value...] [-- poketerm args...]: poketerm on the "new computer"
    local -a envs=(); while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done; [[ $# -gt 0 ]] && shift
    env -i HOME="$n_home" USER=tester2 PATH="$n_home/.local/bin:$PATH" POKETERM_NO_UPDATE_CHECK=1 ${envs[@]+"${envs[@]}"} \
        zsh "$N_APP/poketerm" "$@" > "$LAST_OUT" 2>&1 < /dev/null
}
n_run; n_run      # it has progress of its own
n_before="$(cat "$N_APP/pokedex.txt")"
n_run POKETERM_PASSPHRASE="wrong passphrase" -- --import "$x_file" --yes
check "a wrong passphrase is refused, and nothing changes" \
    bash -c 'grep -q "Wrong passphrase" "$1" && [[ "$(cat "$2")" == "$3" ]]' _ "$LAST_OUT" "$N_APP/pokedex.txt" "$n_before"
n_run POKETERM_PASSPHRASE="$POKETERM_PASSPHRASE" -- --import "$x_file"
check "without a terminal to confirm in (and no --yes), it asks rather than replacing" \
    bash -c 'grep -q "confirm" "$1" && [[ "$(cat "$2")" == "$3" ]]' _ "$LAST_OUT" "$N_APP/pokedex.txt" "$n_before"
n_run POKETERM_PASSPHRASE="$POKETERM_PASSPHRASE" -- --import "$x_file" --yes
eq    "import succeeds" "$?" "0"
for f in pokedex.txt trainer.json settings.conf trade/identity.key trade/identity.crt; do
    check "...$f arrives exactly" same_file "$APP/$f" "$N_APP/$f"
done
# Every story arrives unchanged. (Import then brings the stories in line with the
# Pokedex, which may add lines at the end if they'd drifted - as they have here,
# after the Pokedex was edited by hand earlier in these tests.)
check "...every Pokemon's story arrives unchanged (pokemon.log)" \
    bash -c '[[ "$(head -c "$(wc -c < "$1")" "$2")" == "$(cat "$1")" ]]' _ "$APP/pokemon.log" "$N_APP/pokemon.log"
check "...the private key stays private (0600)" bash -c '[[ "$(stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1")" == 600 ]]' _ "$N_APP/trade/identity.key"
check "...the new computer's own progress was backed up first" \
    bash -c 'f=$(ls -d "$1"/backups/*-before-import 2>/dev/null | head -1) && [[ "$(cat "$f/pokedex.txt")" == "$2" ]]' _ "$N_APP" "$n_before"
n_dex="$(cat "$N_APP/pokedex.txt")"; n_run
check "...and poketerm carries on catching there (Pokedex trusted, not 'changed outside')" \
    bash -c '[[ "$(cat "$1")" != "$2" ]] && ! grep -qi "outside" "$3"' _ "$N_APP/pokedex.txt" "$n_dex" "$LAST_OUT"
unset POKETERM_PASSPHRASE
chmod -R u+w "$n_home" "$x_dir" 2>/dev/null; rm -rf -- "$n_home" "$x_dir"; rm -f "$APP/settings.conf"

# -----------------------------------------------------------------------------
section "Updating keeps progress and settings"
xp_before="$(trainer_field "['total_xp']")"; dex_before="$(cat "$APP/pokedex.txt")"
cp "$T_HOME/.zshrc" "$SANDBOX/zshrc.before"
if installer --update; then pass "update succeeds"; else fail "update succeeds" "$(tail -5 "$SANDBOX/install.log")"; fi
eq    "XP kept"                        "$(trainer_field "['total_xp']")" "$xp_before"
eq    "pokedex kept"                   "$(cat "$APP/pokedex.txt")" "$dex_before"
check "zshrc unchanged by update"      same_file "$T_HOME/.zshrc" "$SANDBOX/zshrc.before"
check "progress backed up before update" test -f "$(ls -d "$APP"/backups/*/ | tail -n 1)trainer.json"
poketerm_run
eq    "first terminal after update works" "$?" "0"

# -----------------------------------------------------------------------------
section "poketerm's own Python"
pinned="$(sed -n 's/^PYTHON_RUNTIME_VERSION="\(.*\)"/\1/p' "$REPO_ROOT/install.sh")"
if [[ -x "$RUNTIME/bin/python3" ]]; then
    eq    "pinned Python installed"               "$("$RUNTIME/bin/python3" -c 'import sys; print(sys.version.split()[0])')" "$pinned"
    check "it has TLS 1.3 (needed for trading)"   "$RUNTIME/bin/python3" -c 'import ssl, sys; sys.exit(0 if ssl.HAS_TLSv1_3 else 1)'
    check "pokedex command runs on it"            grep -q 'poketerm-python/bin/python3' "$T_HOME/.local/bin/pokedex"
    check "pokedex command works"                 env -i HOME="$T_HOME" PATH="/usr/bin:/bin" bash -c '"$1" --help >/dev/null' _ "$T_HOME/.local/bin/pokedex"
    poketerm_run
    eq    "poketerm works with no python3 on PATH" "$?" "0"
    installer --update
    check "updating doesn't download it again"    bash -c '! grep -q "one-time download" "$1"' _ "$SANDBOX/install.log"
    # A bad download (here: a corrupted cached copy) is never used.
    bad_cache="$SANDBOX/bad-cache"; mkdir -p "$bad_cache"
    for f in "$POKETERM_DOWNLOAD_CACHE"/cpython-*; do [[ -f "$f" ]] && printf 'not python' > "$bad_cache/$(basename "$f")"; done
    (export POKETERM_DOWNLOAD_CACHE="$bad_cache"; export HOME="$SANDBOX/home2"; mkdir -p "$HOME"
     HOME="$HOME" POKETERM_REAL_HOME="$HOME" POKETERM_ZSHRC="$HOME/.zshrc" bash "$REPO_ROOT/install.sh" > "$SANDBOX/install2.log" 2>&1)
    check "a corrupted download is rejected (a verified one is fetched instead)" \
        "$SANDBOX/home2/.local/share/poketerm-python/bin/python3" -c 'pass'
    chmod -R u+w "$SANDBOX/home2" 2>/dev/null; rm -rf -- "$SANDBOX/home2" "$bad_cache"
    # Without it (unsupported machine, failed download) the system python3 is used.
    mv "$RUNTIME" "$RUNTIME.away"
    poketerm_run
    eq    "without it, poketerm falls back to the system python3" "$?" "0"
    check "...and so does the pokedex command"    env -i HOME="$T_HOME" PATH="$PATH" bash -c '"$1" --help >/dev/null' _ "$T_HOME/.local/bin/pokedex"
    mv "$RUNTIME.away" "$RUNTIME"
else
    echo "  - skipped: poketerm's Python isn't available for $(uname -sm) (using the system python3)"
fi

# -----------------------------------------------------------------------------
echo ""
if (( FAIL == 0 )); then
    echo "${G}All $PASS checks passed.${N}"
else
    echo "${R}$FAIL of $((PASS + FAIL)) checks failed.${N} Re-run with -v for output."
fi
exit $(( FAIL > 0 ))
