#!/usr/bin/env python3

import json
import sys
import re
import os
import readchar
import urllib.request
import json
import pokedex
from pathlib import Path
from datetime import date, timedelta

# -----------------------
# Paths
# -----------------------
TRAINER_FILE = Path.home() / ".local/share/poketerm/trainer.json"
GEN_DIR = Path.home() / ".local/share/poketerm/gen_files"

def clear():
    sys.stdout.write("\033[2J\033[H\033[3J")
    sys.stdout.flush()

# -----------------------
# ANSI colours
# -----------------------
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RESET   = "\033[0m"
INVERT  = "\033[7m"

COLOR_MAP = {
    "black": 242,   # Soft Gray
    "blue": 117,    # Pastel Sky Blue
    "brown": 180,   # Soft Tan
    "gray": 252,    # Very Light Gray
    "green": 114,   # Sage/Pastel Green (matches your image)
    "pink": 218,    # Soft Rose
    "purple": 147,  # Lavender
    "red": 210,     # Soft Coral
    "white": 255,   # Bright White
    "yellow": 229   # Cream Yellow
}

# -----------------------
# Regions & Towns
# -----------------------
HOME_TOWNS = {
    "Kanto": [
        "Pallet Town", "Viridian City", "Pewter City",
        "Cerulean City", "Vermilion City"
    ],
    "Johto": [
        "New Bark Town", "Cherrygrove City", "Violet City",
        "Azalea Town", "Goldenrod City"
    ],
    "Hoenn": [
        "Littleroot Town", "Oldale Town", "Petalburg City",
        "Rustboro City", "Mauville City"
    ],
    "Sinnoh": [
        "Twinleaf Town", "Sandgem Town", "Jubilife City",
        "Oreburgh City", "Floaroma Town"
    ],
    "Unova": [
        "Nuvema Town", "Accumula Town", "Striaton City",
        "Nacrene City", "Castelia City"
    ],
    "Kalos": [
        "Vaniville Town", "Aquacorde Town", "Santalune City",
        "Lumiose City", "Camphrier Town"
    ],
    "Alola": [
        "Iki Town", "Hau'oli City", "Heahea City",
        "Paniola Town", "Konikoni City"
    ],
    "Galar": [
        "Postwick", "Wedgehurst", "Motostoke",
        "Hammerlocke", "Wyndon"
    ]
}

# -----------------------
# Trainer Ranks
# -----------------------

RANK_TIERS = {
    (0, 5):    ("Beginner Trainer", "gray"),
    (6, 10):   ("Rookie Trainer", "green"),
    (11, 15):  ("Ace Trainer", "blue"),
    (16, 20):  ("Gym Challenger", "brown"),
    (21, 25):  ("Gym Leader", "black"),
    (26, 30):  ("Elite Trainer", "pink"),
    (31, 35):  ("Elite Four", "red"),
    (36, 40):  ("Champion", "yellow"),
    (41, 45):  ("Regional Master", "blue"),
    (46, 49):  ("Pokedex Master", "white"),
    (50, float("inf")): ("Grand Champion", "purple"),
}

# -----------------------
# Helpers
# -----------------------
def choose_from_list(title, options):
    print(f"\n{title}")
    print("-" * len(title))
    for i, opt in enumerate(options, 1):
        print(f"{i}) {opt}")

    while True:
        choice = input("Select option: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]

def get_pokemon_color_code(pokemon_name: str) -> int:
    name = normalize_name(pokemon_name)
    cache_path = Path.home() / f".local/share/poketerm/cache/api_data/{name}.json"
    
    data = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
            if "color" in data:
                return COLOR_MAP.get(data["color"], 15)
        except Exception:
            pass

    try:
        url = f"https://pokeapi.co/api/v2/pokemon-species/{name}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req, timeout=5) as response:
            species_data = json.loads(response.read().decode())
            color_name = species_data.get("color", {}).get("name", "white")
            
            data["color"] = color_name
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(data, f)
            
            return COLOR_MAP.get(color_name, 15)
    except Exception:
        return 15

def add_xp(xp_amount):
    if TRAINER_FILE.exists():
        trainer = load_trainer_profile(update_stats=True)
        trainer.setdefault("xp", 0)
        trainer.setdefault("total_xp", 0)
        trainer.setdefault("level", 0)
        
        trainer["xp"] += xp_amount
        trainer["total_xp"] += xp_amount
        
        while True:
            if trainer["level"] >= 50:
                cost = 25500
            else:
                cost = (trainer["level"] + 1) * 500
                
            if trainer["xp"] >= cost:
                trainer["xp"] -= cost
                trainer["level"] += 1
            else:
                break
                
        TRAINER_FILE.write_text(json.dumps(trainer, indent=2))

def get_rank(level):
    for (low, high), (title, colour_name) in RANK_TIERS.items():
        if low <= level <= high:
            colour_id = COLOR_MAP[colour_name]
            return f"\033[48;5;{colour_id}m\033[38;5;16m ✦ {title} ✦ {RESET}"

def update_streak():
    if not TRAINER_FILE.exists():
        return

    trainer = load_trainer_profile(update_stats=True)
    # today = (date.today() + timedelta(days=5)) #FOR TESTING
    today = date.today()
    today_str = today.isoformat()
    last_date_str = trainer['streak']['last_active_date']
    last_date = date.fromisoformat(last_date_str)

    if last_date < today:
        trainer['daily_catch']['count'] = 0
        trainer['daily_catch']['date'] = today_str

    if last_date == today:
        pass 
    elif last_date == today - timedelta(days=1):
        trainer['streak']['current'] += 1
    else:
        trainer['streak']['current'] = 1

    if trainer['streak']['current'] > trainer['streak']['longest']:
        trainer['streak']['longest'] = trainer['streak']['current']

    trainer['streak']['last_active_date'] = today_str

    TRAINER_FILE.write_text(json.dumps(trainer, indent=2))

# -----------------------
# Favourite Pokemon Selection
# -----------------------
POKEMON_LOOKUP = {}
MAX_NATIONAL_DEX = 905

def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())

def load_all_generations():
    """Load all gen files in alphabetical order"""
    all_pokemon = []
    dex = 1
    for gen_file in sorted(GEN_DIR.glob("*.txt")):
        with open(gen_file) as f:
            for line in f:
                name = line.strip()
                if name:
                    all_pokemon.append((dex, name))
                    dex += 1
    return all_pokemon

def build_global_lookup():
    global POKEMON_LOOKUP
    all_pokemon = load_all_generations()
    lookup = {}
    for dex, name in all_pokemon:
        key = normalize_name(name)
        lookup[key] = dex
    POKEMON_LOOKUP = lookup
    return all_pokemon

def choose_favourite_pokemon(max_dex, all_pokemon_list):
    print("\nChoose 6 favourite Pokemon")
    print("• Enter a Pokemon name (e.g. Bulbasaur)")
    print(f"• Or enter a National Dex number (1–{max_dex})")
    print("• No duplicates\n")

    favourites = []

    while len(favourites) < 6:
        raw = input(f"Favourite {len(favourites)+1}/6: ").strip()
        if not raw:
            continue

        dex = None
        key = normalize_name(raw)
        if key in POKEMON_LOOKUP:
            dex = POKEMON_LOOKUP[key]
        elif raw.isdigit():
            dex = int(raw)
            if dex < 1 or dex > max_dex:
                print(f"  ✖ Dex number must be between 1 and {max_dex}")
                continue
        else:
            print("  ✖ Unknown Pokemon name")
            continue

        if dex in favourites:
            print("  ✖ Pokemon already selected")
            continue

        favourites.append(dex)
        name = next(n for d, n in all_pokemon_list if d == dex)
        print(f"  ✔ Added #{dex} {name.title()}")

    return favourites

# -----------------------
# Trainer Creation
# -----------------------
def create_trainer_profile(editing=False):
    if editing:
        print("\nEditing profile...")
    else:
        print("\nWelcome, Trainer!")
        print("-----------------")

    name = ""
    while not (name.isalpha() and len(name) <= 12):
        name = input("Trainer name (letters only): ").strip().title()

    regions = list(HOME_TOWNS.keys())
    region = choose_from_list("Choose your home region", regions)

    hometown = choose_from_list(
        f"Choose your home town ({region})",
        HOME_TOWNS[region]
    )

    all_pokemon_list = build_global_lookup()

    favourites = choose_favourite_pokemon(
        max_dex=MAX_NATIONAL_DEX,
        all_pokemon_list=all_pokemon_list
    )

    trainer = load_trainer_profile(True)

    trainer = {
        "name": name,
        "region": region,
        "hometown": hometown,
        "created": trainer['created'] if editing else date.today().isoformat(),
        "favourites": favourites,
        "level": trainer['level'],
        "xp": trainer['xp'],
        "total_xp": trainer['total_xp'],
        "streak": {
            "current": trainer['streak']['current'],
            "longest": trainer['streak']['longest'],
            "last_active_date": trainer['streak']['last_active_date'] if editing else date.today().isoformat()
        },
        "daily_catch": {
            "date": trainer['daily_catch']['date'] if editing else date.today().isoformat(),
            "count": trainer['daily_catch']['count']
        },
        "hall_of_fame": trainer['hall_of_fame']
    }

    TRAINER_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRAINER_FILE.write_text(json.dumps(trainer, indent=2))

    return trainer

# -----------------------
# Load Trainer
# -----------------------
def load_trainer_profile(create_profile=False, update_stats=False):
    try:
        trainer = json.loads(TRAINER_FILE.read_text())
        if trainer['name'] != "None":
            return trainer
        elif create_profile and trainer['name'] == "None":
            return trainer
        elif update_stats and trainer['name'] == "None":
            return trainer
        else:
            return create_trainer_profile()
    except Exception:
        print("Trainer profile corrupted — recreating.")
        return create_trainer_profile()

# -----------------------
# Trainer Profile Renderer
# -----------------------
def render_trainer_profile(gen_progress):
    clear()
    WIDTH = 59
    ANSI_RE = re.compile(r'\033\[[0-9;]*m')

    trainer = load_trainer_profile()
    all_pokemon_list = build_global_lookup()

    def visible_len(s):
        return len(ANSI_RE.sub('', s))

    def line(text="", color=RED):
        pad_length = WIDTH - visible_len(text)
        return f"{color}│{RESET} {text}{' ' * pad_length} {color}│{RESET}"

    def dashed_line():
        return f"{RED}├{'─'*(WIDTH+2)}┤{RESET}"

    def bar(done, total, width=20):
        if total == 0:
            return "░" * width
        
        filled = int(width * done / total)
        
        fill_color = GREEN if done == total else YELLOW if done > 0 else RED
        
        if done > 0 and filled == 0:
            filled = 1
            
        return f"{fill_color}{'█' * filled}{RESET}{'░' * (width - filled)}"

    def get_pokemon_name(dex):
        return next((n for d, n in all_pokemon_list if d == dex), f"#{dex}")

    favourites = trainer.get("favourites", [])
    lvl = trainer.get('level', 0)
    current_xp = trainer.get('xp', 0)
    lifetime_xp = trainer.get('total_xp', 0)
    rank = get_rank(lvl)
    streak_current = trainer.get('streak').get('current', 0)
    streak_longest = trainer.get('streak').get('longest', 0)
    streak_last_date = trainer.get('streak').get('last_active_date', "1996-02-27")
    daily_catch_count = trainer.get('daily_catch').get('count', 0)

    if lvl >= 50:
        next_lvl_req = 25500
    else:
        next_lvl_req = (lvl + 1) * 500
        
    xp_bar_display = bar(current_xp, next_lvl_req, width=25)

    should_redraw = True

    while True:
        if should_redraw:
            clear()
            os.system("clear" if os.name == "posix" else "cls")

            print(f"{RED}┌" + "─" * (WIDTH + 2) + f"┐{RESET}")
            print(line(f"{INVERT}  ✦ TRAINER PROFILE ✦  {RESET}"))
            print(line())
            print(line(f"Name:        {trainer['name']}"))
            print(line(f"Region:      {trainer['region']}"))
            print(line(f"Home Town:   {trainer['hometown']}"))
            print(line(f"Started:     {trainer['created']}"))
            print(line(f"Level:       {lvl}"))
            print(line(f"Progress:    [{xp_bar_display}] {current_xp}/{next_lvl_req}"))
            print(line(f"Lifetime XP: {lifetime_xp:,}"))
            print(line(f"Rank:        {rank}"))
            print(dashed_line())
            print(line(f"{INVERT}  ✦ Collection Streaks ✦  {RESET}"))
            print(line())
            print(line(f"Daily Catch Count:    {daily_catch_count}"))
            print(line(f"Current Catch Streak: {streak_current}"))
            print(line(f"Longest Catch Streak: {streak_longest}"))
            print(line(f"Last Catch Date:      {streak_last_date}"))
            print(dashed_line())
            print(line(f"{INVERT}  ✦ Pokedex Completion ✦  {RESET}"))
            print(line())

            for gen in sorted(gen_progress):
                caught, total = gen_progress[gen]
                status = "✔" if caught == total and total > 0 else ""
                fill_color = GREEN if caught == total and total > 0 else YELLOW if caught > 0 else RED
                progress = f"Gen {gen:<2} [{bar(caught, total)}] {fill_color} {caught}/{total} {status}{RESET}"
                print(line(progress))

            print(line())
            print(line("Shiny chance for completed pokedex's: 1/10."))
            print(dashed_line())
            print(line(f"{INVERT}  ✦ Favourite Pokemon ✦  {RESET}"))
            print(line())
            for i, dex in enumerate(favourites, 1):
                name = get_pokemon_name(dex)
                bg_val = get_pokemon_color_code(name)
                ansi = f"\033[48;5;{bg_val}m\033[38;5;16m"
                print(line(f"{i}) {ansi} {name.title().center(15)} {RESET} (#{dex})"))

            print(dashed_line())
            print(line("[1-6] View Favourite   [E] Edit Profile   [B] Back"))
            print(f"{RED}└" + "─" * (WIDTH + 2) + f"┘{RESET}")

        key = readchar.readkey().lower()

        if key == "b":
            should_redraw = True
            break
        elif key == "e":
            clear()
            create_trainer_profile(True)
            render_trainer_profile(gen_progress=gen_progress)
            should_redraw = True
            break
        elif key in map(str, range(1, len(favourites)+1)):
            idx = int(key) - 1
            dex = favourites[idx]
            name = get_pokemon_name(dex)
            clear()
            pokedex.draw_details(name, 1, 1)
            clear()
            should_redraw = True
        else:
            should_redraw = False
            pass