#!/usr/bin/env python3
import sys
import os

try:
    import readchar
except ImportError:
    print("\n\033[31mError: 'readchar' is not installed.\033[0m")
    print("Please run: \033[32mpip3 install -r requirements.txt\033[0m from the root of the poketerm repository.\n")
    sys.exit(1)

import readchar
import json
import subprocess
import re
import urllib.request
import signal
from pathlib import Path

# -----------------------
# Paths
# -----------------------
POKEDEX_FILE = Path.home() / ".local/share/poketerm/pokedex.txt"
GEN_DIR = Path.home() / ".local/share/poketerm/gen_files"

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
clear()

def hide_cursor():
    sys.stdout.write("\033[?25l")

def show_cursor():
    sys.stdout.write("\033[?25h")

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

# -----------------------
# Rendering
# -----------------------
BOX_WIDTH = 37
PAGE_SIZE = 15

def count_cell(icon, color, value):
    return f"{color}{icon}{RESET} {value:<3}"

def draw(gen, page, cursor, pkm_list, pokedex):
    sys.stdout.write("\033[H")

    total = len(pkm_list)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    start = (page - 1) * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    unique = sum(
        1 for p in pkm_list
        if pokedex.get(p, {}).get("normal", 0) > 0 or pokedex.get(p, {}).get("shiny", 0) > 0
    )

    print(f"{RED}┌{'─'*BOX_WIDTH}┐{RESET}")
    title = f"Generation {gen} Pokedex"
    pad = (BOX_WIDTH - len(title)) // 2
    print(f"{RED}│{RESET}{' '*pad}{YELLOW}{title}{RESET}{' '*(BOX_WIDTH-len(title)-pad)}{RED}│{RESET}")
    print(f"{RED}├{'─'*BOX_WIDTH}┤{RESET}")

    caught = f"Pokemon Caught: {unique}/{total}"
    pad = (BOX_WIDTH - len(caught)) // 2
    print(f"│{' '*pad}{GREEN if unique else RED}{caught}{RESET}{' '*(BOX_WIDTH-len(caught)-pad)}│")

    print("│ ----------------------------------- │")
    print("│ Pokemon     Normal  Shiny           │")
    print("│ ----------------------------------- │")

    max_name_len = max(len(pkm) for pkm in pkm_list)
    col1_width = max(max_name_len, 10)

    rows_printed = 0
    for i, pkm in enumerate(pkm_list[start:end]):
        idx = start + i
        sel = INVERT if idx == cursor else ""

        n = pokedex.get(pkm, {}).get("normal", 0)
        s = pokedex.get(pkm, {}).get("shiny", 0)

        ncell = count_cell("✔", GREEN, n) if n > 0 else count_cell("✖", RED, n)
        scell = count_cell("✔", YELLOW, s) if s > 0 else count_cell("✖", RED, s)

        row = f"{pkm:<{col1_width}}   {ncell}   {scell}"
        print(f"{sel}│ {row:<{BOX_WIDTH+16}} │{RESET}")
        rows_printed += 1

    for _ in range(PAGE_SIZE - rows_printed):
        print(f"│ {' '*(BOX_WIDTH-2)} │")

    print("│ ----------------------------------- │")
    footer = f"Page {page}/{pages} (↑↓ ←→ Enter q)"
    print(f"│ {footer:<{BOX_WIDTH-2}} │")
    print(f"{RED}└{'─'*BOX_WIDTH}┘{RESET}")
    sys.stdout.write("\033[J")
    sys.stdout.flush()

def draw_details(name, has_normal, has_shiny):
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
            clear()
            print(f"{RED}Failed to fetch Pokemon data.{RESET}")
            readchar.readkey()
            return

    pid = data["id"]
    types = "/".join(t.replace("-", " ").title() for t in sorted(data["types"]))
    height = data["height"]
    weight = data["weight"]
    stats = [(s[0].replace("-", " ").title(), s[1]) for s in data["stats"]]

    sprites = {"normal": [], "shiny": []}
    for variant in ("normal", "shiny"):
        owned = has_normal if variant == "normal" else has_shiny
        if not owned: continue
        cache_sprite = CACHE_DIR / f"sprite_data/{name.lower()}_{variant}.txt"
        if cache_sprite.exists():
            sprites[variant] = cache_sprite.read_text().splitlines()
        else:
            try:
                args = ["pokemon-colorscripts", "--no-title", "-n", name]
                if variant == "shiny": args.insert(2, "-s")
                sprites[variant] = subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True).splitlines()
                cache_sprite.write_text("\n".join(sprites[variant]))
            except Exception:
                sprites[variant] = []

    box_width = 80
    inner = box_width - 2

    def hr(): print(f"{RED}┌{'─'*inner}┐{RESET}")
    def mid(): print(f"{RED}├{'─'*inner}┤{RESET}")

    try:
        sys.stdout.write("\033[?7l")
        
        clear() 

        while True:
            sys.stdout.write("\033[H")
            
            hr()
            title = " Pokedex Entry "
            pad = (inner - len(title)) // 2
            print(f"{RED}│{RESET}{' '*pad}{YELLOW}{title}{RESET}{' '*(inner-len(title)-pad)}{RED}│{RESET}")
            mid()

            name_line = f"No. {pid} / {name.title()}"
            pad = (inner - len(name_line)) // 2
            print(f"{RED}│{RESET}{' '*pad}{YELLOW}{name_line}{RESET}{' '*(inner-len(name_line)-pad)}{RED}│{RESET}")
            mid()

            sprite_label = f"Sprite: {current.title()}"
            pad = (inner - len(sprite_label)) // 2
            print(f"{RED}│{RESET}{' '*pad}{YELLOW}{sprite_label}{RESET}{' '*(inner-len(sprite_label)-pad)}{RED}│{RESET}")
            mid()

            for line in sprites.get(current, []):
                plain = strip_ansi(line)
                vis = len(plain)
                pad_total = inner - vis
                left = pad_total // 2
                right = pad_total - left
                print(f"{RED}│{RESET}{' '*left}{line}{' '*right}{RED}│{RESET}")

            mid()
            print(f"{RED}│{RESET} {GREEN}Type(s):{RESET} {types:<{inner-10}}{RED}│{RESET}")
            print(f"{RED}│{RESET} {GREEN}Height:{RESET}  {height:<{inner-10}}{RED}│{RESET}")
            print(f"{RED}│{RESET} {GREEN}Weight:{RESET}  {weight:<{inner-10}}{RED}│{RESET}")
            print(f"{RED}│{RESET} {GREEN}Base Stats:{RESET}{' '*(inner-12)}{RED}│{RESET}")

            for stat, val in stats:
                line = f"  * {GREEN}{stat}:{RESET} {val}"
                pad = inner - len(strip_ansi(line)) - 1
                print(f"{RED}│{RESET} {line}{' '*pad}{RED}│{RESET}")

            print(f"{RED}└{'─'*inner}┘{RESET}")
    
            sys.stdout.write("\033[J")
            sys.stdout.flush()

            if has_normal and has_shiny:
                print("\nUse ←/→ to toggle normal/shiny, any other key to return…", end="", flush=True)
                key = readchar.readkey()
                if key in (readchar.key.LEFT, readchar.key.RIGHT):
                    current = "normal" if current == "shiny" else "shiny"
                else: 
                    break
            else:
                print("\nPress any key to return…", end="", flush=True)
                readchar.readkey()
                break
    finally:
        sys.stdout.write("\033[?7h")
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)

# -----------------------
# Help
# -----------------------
def print_help():
    print(f"""
    {YELLOW}Poketerm Pokedex Help{RESET}
    -----------------------
    Usage:
    {GREEN}pokedex [gen]{RESET}    Open the Pokedex for a specific generation (1-8).
    {GREEN}pokedex --help{RESET}   Display this help message.

    Controls:
    {GREEN}↑/↓{RESET}           Move the cursor up and down.
    {GREEN}←/→{RESET}           Change pages quickly.
    {GREEN}Enter{RESET}         View detailed stats and sprites (if caught).
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
    page, cursor = 1, 0

    hide_cursor()
    try:
        while True:
            draw(gen, page, cursor, pkm_list, pokedex)
            key = readchar.readkey()

            if key == "q": break
            elif key == readchar.key.UP:
                cursor = max(0, cursor - 1)
                if cursor < (page - 1) * PAGE_SIZE: page -= 1
            elif key == readchar.key.DOWN:
                cursor = min(len(pkm_list) - 1, cursor + 1)
                if cursor >= page * PAGE_SIZE: page += 1
            elif key == readchar.key.RIGHT:
                page = min(page + 1, (len(pkm_list)-1)//PAGE_SIZE + 1)
                cursor = (page - 1) * PAGE_SIZE
            elif key == readchar.key.LEFT:
                page = max(1, page - 1)
                cursor = (page - 1) * PAGE_SIZE
            elif key == readchar.key.ENTER:
                pkm = pkm_list[cursor]
                n = pokedex.get(pkm, {}).get("normal", 0)
                s = pokedex.get(pkm, {}).get("shiny", 0)
                if n > 0 or s > 0:
                    clear()
                    draw_details(pkm, n > 0, s > 0)
                    clear()
                else:
                    clear()
                    print(f"{RED}You must catch a normal or shiny version to view more information!{RESET}")
                    print("\nPress any key to return…")
                    readchar.readkey()
                    clear()
    finally:
        show_cursor()
        print(RESET)

if __name__ == "__main__":
    main()