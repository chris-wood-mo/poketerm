#!/usr/bin/env python3
import sys
import os

import readchar
import json
import subprocess
import re
import urllib.request
import signal
import trainer
import achievements
from pathlib import Path

# -----------------------
# Paths
# -----------------------
POKEDEX_FILE = Path.home() / ".local/share/poketerm/pokedex.txt"
GEN_DIR = Path.home() / ".local/share/poketerm/gen_files"
TRAINER_FILE = Path.home() / ".local/share/poketerm/trainer.json"

# -----------------------
# ANSI colours (EXACT)
# -----------------------
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RESET   = "\033[0m"
INVERT  = "\033[7m"

# -----------------------
# Terminal helpers
# -----------------------
def clear():
    # \033[2J: Clear entire screen
    # \033[H: Move cursor to home (top-left)
    # \033[3J: Clear scrollback buffer
    sys.stdout.write("\033[2J\033[H\033[3J")
    sys.stdout.flush()

def hide_cursor():
    sys.stdout.write("\033[?25l")

def show_cursor():
    sys.stdout.write("\033[?25h")

GO_COLORSCRIPTS = Path.home() / ".local/share/pokemon-go-colorscripts/pokemon-go-colorscripts"

def colorscripts_cmd():
    """Prefer the much faster Go build; it prints identical sprites."""
    if os.access(GO_COLORSCRIPTS, os.X_OK):
        return str(GO_COLORSCRIPTS)
    import shutil
    return shutil.which("pokemon-go-colorscripts") or "pokemon-colorscripts"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")

def strip_ansi(s):
    return ANSI_RE.sub("", s)

# -----------------------
# Data loading
# -----------------------
def load_generation(gen):
    f = GEN_DIR / f"gen{gen}_list.txt"
    if not f.exists():
        sys.exit(f"Generation {gen} not found")
    return [l.strip() for l in f.read_text().splitlines() if l.strip()]

def load_pokedex():
    data = {}
    if not POKEDEX_FILE.exists():
        return data

    for line in POKEDEX_FILE.read_text().splitlines():
        parts = line.split()
        name = parts[0]
        if "(shiny)" in parts:
            data.setdefault(name, {})["shiny"] = int(parts[-1])
        else:
            data.setdefault(name, {})["normal"] = int(parts[-1])
    return data

def get_all_gen_progress():
    pokedex_data = load_pokedex()
    progress = {}
    
    for gen in range(1, 9):
        try:
            # Load the list for this specific generation
            pkm_list = load_generation(gen)
            total = len(pkm_list)
            
            # Count how many in this list have at least one normal or shiny entry
            caught = sum(
                1 for p in pkm_list 
                if pokedex_data.get(p, {}).get("normal", 0) > 0 
                or pokedex_data.get(p, {}).get("shiny", 0) > 0
            )
            
            progress[gen] = (caught, total)
        except Exception:
            # If a gen file is missing, default to 0
            progress[gen] = (0, 0)
            
    return progress

# -----------------------
# Rendering
# -----------------------
LIST_WIDTH = 40          # the list screen's width inside the device
LIST_HEADER = 6          # rows above the list: title, gap, caught count, progress bar, rule, column names
MAX_PAGE = 15            # list rows when the window has room
GREY = "\033[38;5;245m"
BOLD = "\033[1m"

_dex_numbers = None


def dex_number(name):
    """National Pokedex number, from the generation lists."""
    global _dex_numbers
    if _dex_numbers is None:
        _dex_numbers, n = {}, 0
        for gen in range(1, 9):
            try:
                for p in load_generation(gen):
                    n += 1
                    _dex_numbers.setdefault(p, n)
            except SystemExit:
                pass
    return _dex_numbers.get(name, 0)


REGIONS = ["Kanto", "Johto", "Hoenn", "Sinnoh", "Unova", "Kalos", "Alola", "Galar"]


def ask_nickname(rec, name):
    """Ask for a nickname on the device's screen. Empty clears it; returns the name kept."""
    import collection, device
    error = ""
    while True:
        lines = [f"{YELLOW}{BOLD}Nickname your {pretty(name)}{RESET}", "",
                 f"{collection.summary(rec)}", "",
                 "Up to 12 letters, numbers, spaces and . ' -",
                 "Leave it empty and press Enter to clear it."]
        if rec.get("nickname"):
            lines.append(f"Current nickname: {rec['nickname']}")
        if error:
            lines += ["", f"{RED}{error}{RESET}"]
        typed = device.prompt(lines, "Nickname:", width=46, info=[f"{BOLD}NICKNAME{RESET}", "",
                              "It travels with this", "Pokemon if you trade it."],
                              keys=[("⏎", "Save")], slots=("Nickname", pretty(name)[:12]),
                              max_len=collection.MAX_NICKNAME,
                              allowed=lambda ch: ch.isalnum() or ch in " .'-")
        try:
            return collection.set_nickname(rec["id"], typed.strip())
        except ValueError as e:
            error = str(e)


def home_region(name):
    """The region a Pokemon comes from (from its generation)."""
    for gen in range(1, 9):
        try:
            if name in load_generation(gen):
                return REGIONS[gen - 1]
        except SystemExit:
            pass
    return "Unknown"


INFO_SOFT = "\033[38;5;65m"                      # lighter text on the green display
INFO_RULE = f"{INFO_SOFT}{'─' * 28}\033[0m"     # a divider on the green display
_region_cache = {}


def region_of(name):
    if name not in _region_cache:
        _region_cache[name] = home_region(name)
    return _region_cache[name]


def types_of(name):
    """A Pokemon's types from the local data cache (empty if not cached yet)."""
    try:
        data = json.loads((Path.home() / f".local/share/poketerm/cache/api_data/{name.lower()}.json").read_text())
        return [t for t in data.get("types", []) if isinstance(t, str)][:2]
    except (OSError, ValueError, AttributeError):
        return []


def pretty(name):
    return name.replace("-", " ").title()


_traded_cache = {}


def traded_from(name):
    """Who you've received this Pokemon from in trades (cached per name)."""
    if name not in _traded_cache:
        try:
            import trade_store
            got = trade_store.received_from(name, False) + trade_store.received_from(name, True)
        except Exception:
            got = []
        _traded_cache[name] = got
    return _traded_cache[name]


def search_pokedex(pokedex):
    """Search all 905 Pokemon by name or number. Returns {"name", "no", "gen"} or None."""
    import search
    entries = []
    for gen in range(1, 9):
        try:
            entries += [{"name": p, "no": dex_number(p), "gen": gen} for p in load_generation(gen)]
        except SystemExit:
            pass

    def describe(e):
        n = pokedex.get(e["name"], {}).get("normal", 0)
        sh = pokedex.get(e["name"], {}).get("shiny", 0)
        mark = (f"{GREEN}✔{RESET}" if n else f"{GREY}·{RESET}") + (f" {YELLOW}★{RESET}" if sh else "  ")
        return f"{e['no']:03d}  {pretty(e['name'])[:16]:<16} {GREY}Gen {e['gen']}{RESET}  {mark}"

    return search.search(entries, "SEARCH THE POKEDEX", describe, width=LIST_WIDTH,
                         slots=("Search", "905 Pokemon"), status="Search")


def poketerm_version():
    """The installed version, e.g. "1.0.0" (from the VERSION file the installer writes)."""
    try:
        return (Path.home() / ".local/share/poketerm/VERSION").read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def settings_page():
    """The settings page (press s on the Pokedex list). Every change is saved at
    once and applies from the next terminal you open."""
    import device, settings
    values = settings.load()
    W = 46
    counts = {}
    for g in settings.ALL_GENERATIONS:
        try:
            counts[g] = len(load_generation(g))
        except SystemExit:
            counts[g] = 0
    toggles = [("system_details", "System details next to your catch",
                ["SYSTEM DETAILS", "", "The OS, host, uptime", "and so on shown next to", "each new Pokemon.", "",
                 "Turning it off also makes", "new terminals a little", "faster."]),
               ("animations", "Trade animations",
                ["TRADE ANIMATIONS", "", "The Poke Ball animations", "when a trade completes.", "",
                 "Off: the trade finishes", "straight away."]),
               ("update_check", "Daily update check",
                ["UPDATE CHECK", "", "Once a day, check for a", "new poketerm version", "and tell you about it.", "",
                 "Off: update when you", "like with", "poketerm --update"])]
    rows = len(settings.ALL_GENERATIONS) + len(toggles)
    cursor, note = 0, ""
    while True:
        lines = [f"{YELLOW}{BOLD}{'SETTINGS':^{W}}{RESET}", f"{GREY}{'─' * W}{RESET}",
                 f"{YELLOW}Catch Pokemon from{RESET}"]
        for i, g in enumerate(settings.ALL_GENERATIONS):
            on = g in values["generations"]
            box = f"{GREEN}[✔]{RESET}" if on else f"{GREY}[ ]{RESET}"
            text = f"{box} Gen {g}  {REGIONS[g - 1]:<7} {GREY}{counts[g]} Pokemon{RESET}"
            lines.append(row(text, i == cursor, W))
        lines += ["", f"{YELLOW}Other{RESET}"]
        for j, (key, label, _) in enumerate(toggles):
            i = len(settings.ALL_GENERATIONS) + j
            state = f"{GREEN}[On ]{RESET}" if values[key] else f"{RED}[Off]{RESET}"
            lines.append(row(f"{state} {label}", i == cursor, W))
        caught_from = len(values["generations"])
        lines += ["", f"{GREY}Changes save straight away and apply from{RESET}",
                  f"{GREY}the next terminal you open.{RESET}",
                  "", f"{GREY}{'─' * W}{RESET}",
                  f"{GREY}{'poketerm ' + poketerm_version():^{W}}{RESET}"]
        if note:
            lines += ["", note]
        if cursor < len(settings.ALL_GENERATIONS):
            g = settings.ALL_GENERATIONS[cursor]
            info = [f"{BOLD}GENERATION {g}{RESET}", REGIONS[g - 1], "", f"{counts[g]} Pokemon", "",
                    "New terminals catch", "from every generation", "that's ticked.", "",
                    f"{caught_from} of 8 ticked"]
        else:
            info = toggles[cursor - len(settings.ALL_GENERATIONS)][2]
        device.show(lines, width=W, info=info,
                    keys=[("↑↓", "Move"), ("⏎", "Change"), ("a", "All gens"), ("b", "Back")],
                    slots=("Settings", f"{caught_from}/8 gens"), lights=(True, False, True),
                    status="Settings", title="Settings")
        key = device.readkey()
        if key is None:
            continue
        note = ""
        if key == readchar.key.UP:
            cursor = (cursor - 1) % rows
        elif key == readchar.key.DOWN:
            cursor = (cursor + 1) % rows
        elif key in (readchar.key.ENTER, " "):
            if cursor < len(settings.ALL_GENERATIONS):
                g = settings.ALL_GENERATIONS[cursor]
                if g in values["generations"]:
                    if len(values["generations"]) == 1:
                        note = f"{RED}At least one generation has to stay on.{RESET}"
                        continue
                    values["generations"] = [x for x in values["generations"] if x != g]
                else:
                    values["generations"] = sorted(values["generations"] + [g])
            else:
                key_name = toggles[cursor - len(settings.ALL_GENERATIONS)][0]
                values[key_name] = not values[key_name]
            settings.save(values)
            note = f"{GREEN}✔ Saved{RESET}"
        elif key == "a":
            values["generations"] = list(settings.ALL_GENERATIONS)
            settings.save(values)
            note = f"{GREEN}✔ All 8 generations on{RESET}"
        elif key in ("b", "q", "\x1b"):
            return


def row(text, selected, width):
    """A settings row; the selected one is highlighted as a single bar."""
    if not selected:
        return f"  {text}"
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return f"{INVERT}> {plain:<{width - 2}}{RESET}"


def list_page_size():
    import device
    return max(3, device.screen_rows(LIST_WIDTH, MAX_PAGE + LIST_HEADER) - LIST_HEADER)


def draw(gen, cursor, pkm_list, pokedex):
    """The Pokedex list, on the device's screen. Returns the page size used."""
    import device
    page_size = list_page_size()
    total = len(pkm_list)
    pages = max(1, (total + page_size - 1) // page_size)
    page = cursor // page_size + 1
    start = (page - 1) * page_size
    caught = sum(1 for p in pkm_list
                 if pokedex.get(p, {}).get("normal", 0) > 0 or pokedex.get(p, {}).get("shiny", 0) > 0)

    W = min(LIST_WIDTH, device.available_width())
    tight = W < 36                              # narrow window: single spaces between columns
    gap, name_w = (" ", max(6, W - 19)) if tight else ("  ", 15)
    # Caught count on the left, percentage on the right, and a full-width
    # progress bar under them (the same gauge style as the stat bars).
    pct = round(100 * caught / total) if total else 0
    count = f"Caught {caught} of {total}"
    bar_w = W - 2
    filled = round(bar_w * caught / total) if total else 0
    if caught and not filled:
        filled = 1                                  # any progress shows
    bar = f"\033[38;5;71m{'▆' * filled}\033[38;5;238m{'▆' * (bar_w - filled)}{RESET}"
    lines = [
        f"{YELLOW}{BOLD}{f'GENERATION {gen} POKEDEX':^{W}}{RESET}",
        "",
        f" {GREEN if caught else RED}{count}{RESET}{' ' * max(1, W - 2 - len(count) - len(f'{pct}%'))}{BOLD}{pct}%{RESET}",
        f" {bar}",
        f"{GREY}{'─' * W}{RESET}",
        f"{GREY} No.{gap}{'Pokemon':<{name_w}}{gap}Normal{gap}Shiny{RESET}" if not tight
        else f"{GREY} No. {'Pokemon':<{name_w}} Norm.  Shiny{RESET}",
    ]
    for idx in range(start, start + page_size):
        if idx >= total:
            lines.append("")
            continue
        p = pkm_list[idx]
        n = pokedex.get(p, {}).get("normal", 0)
        sh = pokedex.get(p, {}).get("shiny", 0)
        ncell = f"{GREEN}✔{RESET} {n:<4}" if n else f"{RED}✖{RESET} {n:<4}"
        scell = f"{YELLOW}★{RESET} {sh:<4}" if sh else f"{RED}✖{RESET} {sh:<4}"
        row = (f" {dex_number(p):03d}{gap}{pretty(p)[:name_w]:<{name_w}}{gap}{ncell}{' ' if tight else '   '}{scell}")
        if idx == cursor:
            plain = re.sub(r"\x1b\[[0-9;]*m", "", row)
            row = f"{INVERT}{plain:<{W}}{RESET}"
        lines.append(row)

    # The selected Pokemon, on the green display
    sel = pkm_list[cursor]
    n = pokedex.get(sel, {}).get("normal", 0)
    sh = pokedex.get(sel, {}).get("shiny", 0)
    info = [f"No. {dex_number(sel):03d}", f"{BOLD}{pretty(sel).upper()}{RESET}"]
    kinds = " · ".join(t.title() for t in types_of(sel))
    info += [f"{kinds}{'  ' if kinds else ''}{INFO_SOFT}{region_of(sel)}{RESET}", INFO_RULE]
    if n or sh:
        info += [f"{'Normal':<10}{'✔' if n else INFO_SOFT + '✖' + RESET}  {n}",
                 f"{'Shiny':<10}{'★' if sh else INFO_SOFT + '✖' + RESET}  {sh}"]
        trades = traded_from(sel)
        if trades:
            peers = list(dict.fromkeys(t["peer"] for t in trades))
            more = f" +{len(peers) - 1}" if len(peers) > 1 else ""
            info += [INFO_RULE, f"Traded from {peers[0][:12]}{more}"]
        info += ["", f"{INFO_SOFT}⏎ to see your {pretty(sel)[:12]}{RESET}"]
    else:
        info += ["Not caught yet", "", f"{INFO_SOFT}Keep opening terminals{RESET}",
                 f"{INFO_SOFT}to find one!{RESET}"]
    keys = [("↑↓", "Move"), ("←→", "Page"), ("⏎", "Entry"), ("/", "Search"), ("s", "Settings"),
            ("p", "Trainer"), ("h", "History"), ("a", "Awards"), ("t", "Trade"), ("q", "Quit")]
    device.show(lines, width=W, info=info, keys=keys,
                slots=(f"Gen {gen}", f"Page {page}/{pages}"),
                lights=(True, sh > 0, n > 0 or sh > 0),
                status=f"{caught}/{total} caught", title=f"Generation {gen}")
    return page_size

def draw_details(name, has_normal, has_shiny, showcase=False):
    """A Pokemon's Pokedex entry. Normally it shows the ones you've caught, each
    with its story. `showcase` (the trainer profile's favourites) shows the
    species itself instead: its normal and shiny looks, type and stats."""
    def handle_resize(signum, frame): pass
    signal.signal(signal.SIGWINCH, handle_resize)

    CACHE_DIR = Path.home() / ".local/share/poketerm/cache"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    current = "normal" if has_normal else "shiny"

    cache_file = CACHE_DIR / f"api_data/{name.lower()}.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        try:
            req = urllib.request.Request(
                f"https://pokeapi.co/api/v2/pokemon/{name.lower()}",
                headers={"User-Agent": "poketerm/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                info = json.loads(r.read().decode())
            data = {
                "id": info["id"],
                "types": [t["type"]["name"] for t in info["types"]],
                "height": info["height"],
                "weight": info["weight"],
                "stats": [(s["stat"]["name"], s["base_stat"]) for s in info["stats"]],
            }
            cache_file.write_text(json.dumps(data))
        except Exception:
            import device
            while True:
                device.show(["", f"{RED}{'Couldn' + chr(39) + 't load this Pokemon' + chr(39) + 's data.':^40}{RESET}", "",
                             f"{'Check your internet connection':^40}", f"{'and try again.':^40}"],
                            width=40, info=[f"{BOLD}{pretty(name).upper()}{RESET}", "", "No data yet"],
                            keys=[("any", "Back")], lights=(True, False, False), status="Offline?")
                if device.readkey() is not None:
                    return

    pid = data["id"]
    types = "/".join(t.replace("-", " ").title() for t in sorted(data["types"]))
    height = data["height"]
    weight = data["weight"]
    stats = [(s[0].replace("-", " ").title(), s[1]) for s in data["stats"]]

    # Sprites exactly as a new terminal shows them (from colorscripts, trimmed).
    import trade_ui
    sprites = {variant: trade_ui.get_sprite({"name": name, "shiny": variant == "shiny"})
               for variant, owned in (("normal", has_normal), ("shiny", has_shiny)) if owned}

    # Your Pokemon of this kind, each with its own story.
    try:
        import collection, trade_store
        if showcase:
            yours = []
        else:
            with trade_store.pokedex_lock():
                yours = collection.of_kind(name)
    except Exception:
        collection, yours = None, []          # the story is a bonus: never break this page
    # Normal and shiny ones are browsed separately; each starts at number 1.
    groups = {"normal": [r for r in yours if not r.get("shiny")], "shiny": [r for r in yours if r.get("shiny")]}
    if not groups[current] and groups["shiny" if current == "normal" else "normal"]:
        current = "shiny" if current == "normal" else "normal"
    picks = {"normal": 0, "shiny": 0}

    import device
    # PokeAPI gives height in decimetres and weight in hectograms.
    facts = [f"{BOLD}TYPE{RESET}    {types}", f"{BOLD}REGION{RESET}  {home_region(name)}",
             f"{BOLD}HEIGHT{RESET}  {height / 10:g} m", f"{BOLD}WEIGHT{RESET}  {weight / 10:g} kg",
             "", f"{BOLD}BASE STATS{RESET}"]
    short = {"Hp": "HP", "Attack": "ATK", "Defense": "DEF", "Special Attack": "SP.ATK",
             "Special Defense": "SP.DEF", "Speed": "SPEED"}
    for stat, val in stats:
        # A gauge per stat: 12 cells against the maximum (255), the filled part
        # coloured by how strong it is, the rest a faint track. "▆" leaves a gap
        # above each bar so the six don't merge into one block.
        filled = max(1, min(12, round(val / 255 * 12)))
        colour = "\033[38;5;160m" if val < 50 else "\033[38;5;178m" if val < 90 else "\033[38;5;28m"
        track = "\033[38;5;108m"
        bar = f"{colour}{'▆' * filled}{track}{'▆' * (12 - filled)}{RESET}"
        facts.append(f"{short.get(stat, stat[:6]):<7}{val:>4} {bar}")

    def story(rec, W):
        """Lines about one of your Pokemon (it may be from before 1.0)."""
        title = pretty(name)
        kind = f"{YELLOW}★ Shiny{RESET}" if rec.get("shiny") else "Normal"
        out = []
        if rec.get("legacy") and not rec.get("caught_at"):
            out.append(f"{GREY}Caught before poketerm 1.0{RESET}")
        else:
            nature = f"{rec['nature']} nature" if rec.get("nature") else f"{GREY}nature unknown{RESET}"
            out.append(f"{kind} · {nature}")
            when = collection.when_text(rec.get("caught_at"))
            if when:
                out.append(f"Caught {when} ({collection.time_of_day(rec['caught_at'])})")
            else:
                out.append(f"{GREY}Date caught unknown{RESET}")
            who, no = rec.get("original_trainer"), rec.get("catch_no")
            by = f"Caught by {who}" if who else f"{GREY}Original trainer unknown{RESET}"
            if rec.get("met_level") is not None:
                by += f" · met at Lv {rec['met_level']}"
            out.append(by)
            mine = not rec.get("history")
            if mine and no == 1:
                out.append(f"{GREEN}Your very first {title}!{RESET}")
            elif mine and no:
                out.append(f"Your {collection.ordinal(no)} {title}")
            elif no:
                out.append(f"{GREY}Their {collection.ordinal(no)} {title}{RESET}")
        for hop in (rec.get("history") or [])[-1:]:
            earlier = len(rec["history"]) - 1
            more = f" {GREY}(+{earlier} earlier trade{'s' if earlier != 1 else ''}){RESET}" if earlier else ""
            out.append(f"{GREEN}Traded: received from {hop.get('from')} on "
                       f"{collection.date_text(hop.get('date'))}{RESET}{more}")
        return out

    try:
        sys.stdout.write("\033[?7l")
        clear()
        while True:
            group = groups[current]
            pick = min(picks[current], max(0, len(group) - 1))
            rec = group[pick] if group else None
            other = "shiny" if current == "normal" else "normal"
            sprite = sprites.get(current)
            if sprite is None:
                sprite = sprites[current] = trade_ui.get_sprite({"name": name, "shiny": current == "shiny"})
            sprite_w = max([device.visible_width(l) for l in sprite] + [0])
            W = min(max(46, sprite_w + 4), device.available_width())    # narrow window: narrow screen
            label = "★ SHINY" if current == "shiny" else "NORMAL"

            def about(r, i):
                """The lines under the sprite for Pokemon `r` (number i+1 of this kind)."""
                if r is None:
                    return [f"{GREY}You don't have a {pretty(name)} right now.{RESET}"]
                n = len(group)
                count = f"{i + 1} of {n} {current}" + ("  ◀ ▶" if n > 1 else "")
                heart = f"{RED}♥{RESET} " if r.get("favourite") else ""
                title_text = (f'"{r["nickname"]}" {GREY}the {pretty(name)}{RESET}' if r.get("nickname")
                              else f"YOUR {pretty(name).upper()}")
                out = [f"{heart}{YELLOW}{BOLD}{title_text}{RESET}  {GREY}{count}{RESET}"] + story(r, W)
                if r.get("favourite"):
                    out.append(f"{RED}♥ Favourite{RESET} {GREY}- protected from trading{RESET}")
                return device.wrap_lines(out, W)          # counted as drawn: long lines wrap

            below = [] if showcase else [f"{GREY}{'─' * W}{RESET}"]
            if not showcase:
                below += about(rec, pick)
                if rec and groups[other]:
                    below.append(f"{GREY}You also have {len(groups[other])} {other} - press s{RESET}")
                # Every one of this Pokemon - normal or shiny, favourite or not,
                # traded or not - gets the same height, so stepping through them,
                # switching with s or pressing f never resizes the page. The spare
                # rows go at the very bottom, so the text itself never has gaps.
                everyone = [r for g in ("normal", "shiny") for r in groups[g]]
                tallest = max([len(about(dict(r, favourite=True), 0)) for r in everyone] or [0])
                tallest += 1 if groups["normal"] and groups["shiny"] else 0     # the "press s" line
                below += [""] * (tallest - (len(below) - 1))

            if showcase:
                keys = [("←→", other.title()), ("b", "Back")]
            else:
                keys = ([("←→", "Next")] if len(group) > 1 else [])
            if groups[other] and not showcase:
                keys.append(("s", other.title()))
            if rec and not showcase:
                keys += [("n", "Nickname"), ("f", "Unfav" if rec.get("favourite") else "Fav")]
            if not showcase:
                keys += [("b", "Back")]

            # The sprite only if the window has room for it and the story:
            # nothing important may scroll off a small window.
            header = [f"{YELLOW}{BOLD}{f'No. {pid:03d}  {name.replace(chr(45), chr(32)).upper()}':^{W}}{RESET}",
                      f"{GREY}{label:^{W}}{RESET}", ""]
            room = device.fit_rows(W, facts, keys) - len(header) - len(below)
            if sprite and len(sprite) <= room and sprite_w <= W:
                pad = (W - sprite_w) // 2
                art = [" " * pad + l for l in sprite]
            elif sprite:
                which = "wider" if sprite_w > W else "taller"     # never draw part of a Pokemon
                art = [f"{GREY}(make the window {which} to see the sprite){RESET}"]
            else:
                art = [f"{GREY}{'(no sprite available)':^{W}}{RESET}"]
            lines = header + art + below

            nature = (rec or {}).get("nature") or ""
            device.show(lines, width=W, info=facts, keys=keys,
                        slots=(f"No. {pid:03d}", (nature or f"{pick + 1} of {len(group)}") if rec else current.title()),
                        lights=(True, current == "shiny", bool(rec)),
                        status=pretty(name), title=f"No. {pid} {pretty(name)}", info_on_screen=True)

            key = device.readkey()
            if key is None:
                continue                      # window resized: redraw to fit
            if showcase:
                if key in (readchar.key.LEFT, readchar.key.RIGHT, "s"):
                    current = other           # normal <-> shiny
                    continue
                break
            if len(group) > 1 and key in (readchar.key.LEFT, readchar.key.RIGHT, readchar.key.UP, readchar.key.DOWN):
                step = 1 if key in (readchar.key.RIGHT, readchar.key.DOWN) else -1
                picks[current] = (pick + step) % len(group)
            elif key == "s" and groups[other]:
                current = other
            elif rec and key == "f":
                collection.set_favourite(rec["id"], not rec.get("favourite"))
                group[pick] = dict(rec, favourite=not rec.get("favourite"))
            elif rec and key == "n":
                group[pick] = dict(rec, nickname=ask_nickname(rec, name))
            else:
                break
    finally:
        sys.stdout.write("\033[?7h")
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)

# -----------------------
# Help
# -----------------------
def print_help():
    print(f"""
    {YELLOW}Poketerm Pokedex Help{RESET}  (poketerm {poketerm_version()})
    -----------------------
    Usage:
    {GREEN}pokedex [gen]{RESET}    Open the Pokedex for a specific generation (1-8).
    {GREEN}pokedex --help{RESET}   Display this help message.

    Controls:
    {GREEN}↑/↓{RESET}           Move the cursor up and down.
    {GREEN}←/→{RESET}           Change pages quickly.
    {GREEN}Enter{RESET}         View detailed stats and sprites (if caught).
    {GREEN}/{RESET}             Search for a Pokemon by name or number (any generation).
    {GREEN}s{RESET}             Settings: generations to catch from, system details, animations, update check.
    {GREEN}p{RESET}             View trainer profile and overall progression.
    {GREEN}h{RESET}             Catch history: first, latest and rarest catch, busiest day, time of day.
    {GREEN}a{RESET}             View achievements.
    {GREEN}t{RESET}             Trade Pokemon with a trainer on your local network.
    {GREEN}q{RESET}             Exit the Pokedex.
        """)

# -----------------------
# Main loop
# -----------------------
def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("-h", "--help", "help"):
            print_help()
            sys.exit(0)
        elif arg.isdigit() and 1 <= int(arg) <= 8:
            gen = int(arg)
        else:
            sys.exit(1)
    else:
        gen = 1
    
    pkm_list = load_generation(gen)
    pokedex = load_pokedex()
    cursor = 0

    hide_cursor()
    try:
        while True:
            page_size = draw(gen, cursor, pkm_list, pokedex)
            import device
            key = device.readkey()
            if key is None:
                continue                      # window resized: redraw to fit
            last = len(pkm_list) - 1

            if key == "q": break
            elif key == "p": 
                trainer.render_trainer_profile(gen_progress=get_all_gen_progress())
                clear()
            elif key == "a": 
                achievements.display_hall_of_fame()
                clear()
            elif key == "t":
                import trade_ui
                clear()
                trade_ui.open_trade_page()
                pokedex = load_pokedex()      # trades change what you own
                _traded_cache.clear()
                clear()
                trade_ui.debug("pokedex: list redrawn")
            elif key == "s":
                settings_page()
                clear()
            elif key == "h":
                import catch_history
                catch_history.history_page()
                clear()
            elif key == "/":
                found = search_pokedex(pokedex)
                if found:
                    if found["gen"] != gen:            # it's in another generation: go there
                        gen, pkm_list = found["gen"], load_generation(found["gen"])
                    cursor = pkm_list.index(found["name"])
                clear()
            elif key == readchar.key.UP:
                cursor = max(0, cursor - 1)
            elif key == readchar.key.DOWN:
                cursor = min(last, cursor + 1)
            elif key == readchar.key.RIGHT:
                cursor = min(last, (cursor // page_size + 1) * page_size)
            elif key == readchar.key.LEFT:
                cursor = max(0, (cursor // page_size - 1) * page_size)
            elif key == readchar.key.ENTER:
                pkm = pkm_list[cursor]
                n = pokedex.get(pkm, {}).get("normal", 0)
                s = pokedex.get(pkm, {}).get("shiny", 0)
                if n > 0 or s > 0:
                    clear()
                    draw_details(pkm, n > 0, s > 0)
                    clear()
                else:
                    import device
                    device.show(["", f"{RED}{'Not caught yet!':^40}{RESET}", "",
                                 f"{'Catch a normal or shiny one to':^40}", f"{'see its Pokedex entry.':^40}"],
                                width=40, info=[f"No. {dex_number(pkm):03d}", f"{BOLD}{pretty(pkm).upper()}{RESET}",
                                                "", "Not caught yet"],
                                keys=[("any", "Back")], slots=(f"Gen {gen}", ""), lights=(True, False, False),
                                status="Keep catching!")
                    device.readkey()
    finally:
        show_cursor()
        print(RESET)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("-h", "--help", "help"):
        print_help()          # no trainer profile needed just to read the help
        sys.exit(0)
    clear()
    trainer.load_trainer_profile()
    main()