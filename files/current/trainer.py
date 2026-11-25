#!/usr/bin/env python3

import json
import sys
import re
import os
import fcntl
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
SETUP_INFO = ["TRAINER SETUP", "", "Answer on the screen", "and press Enter."]


def choose_from_list(title, options, header=()):
    import device
    error = ""
    while True:
        lines = list(header) + [f"{YELLOW}{title}{RESET}", ""]
        lines += [f"  {i:>2}) {opt}" for i, opt in enumerate(options, 1)]
        if error:
            lines += ["", f"{RED}{error}{RESET}"]
        choice = device.prompt(lines, "Select option:", info=SETUP_INFO,
                               slots=("Setup", ""), max_len=3, allowed=str.isdigit).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        error = f"✖ Type a number from 1 to {len(options)}."

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

def _normalize_trainer(trainer):
    if not isinstance(trainer, dict):
        trainer = {}

    trainer.setdefault("name", "None")
    trainer.setdefault("region", "")
    trainer.setdefault("hometown", "")
    trainer.setdefault("created", date.today().isoformat())
    trainer.setdefault("favourites", [])
    trainer.setdefault("level", 0)
    trainer.setdefault("xp", 0)
    trainer.setdefault("total_xp", 0)
    trainer.setdefault("hall_of_fame", [])

    streak = trainer.setdefault("streak", {})
    if not isinstance(streak, dict):
        streak = {}
        trainer["streak"] = streak
    streak.setdefault("current", 0)
    streak.setdefault("longest", 0)
    streak.setdefault("last_active_date", date.today().isoformat())

    daily = trainer.setdefault("daily_catch", {})
    if not isinstance(daily, dict):
        daily = {}
        trainer["daily_catch"] = daily
    daily.setdefault("date", date.today().isoformat())
    daily.setdefault("count", 0)

    return trainer


def _write_trainer(trainer):
    TRAINER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = TRAINER_FILE.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(trainer, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, TRAINER_FILE)


def _keep_corrupt_copy(text):
    """Save an unparseable trainer.json next to it before it's replaced, so
    the player's progress can still be recovered by hand."""
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = TRAINER_FILE.with_name(f"{TRAINER_FILE.name}.corrupt-{stamp}")
    try:
        backup.write_text(text)
    except OSError:
        pass


def _locked_trainer_update(update_fn):
    TRAINER_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = TRAINER_FILE.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if TRAINER_FILE.exists():
                # An unreadable file (OSError) propagates: never overwrite
                # data we couldn't read.
                text = TRAINER_FILE.read_text()
                try:
                    trainer = json.loads(text)
                except json.JSONDecodeError:
                    _keep_corrupt_copy(text)
                    trainer = {}
            else:
                trainer = {}
            updated = update_fn(_normalize_trainer(trainer))
            _write_trainer(updated)
            return updated
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def add_xp(xp_amount):
    def update(trainer):
        trainer["xp"] = trainer.get("xp", 0) + xp_amount
        trainer["total_xp"] = trainer.get("total_xp", 0) + xp_amount

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

        return trainer

    _locked_trainer_update(update)


def get_rank(level):
    for (low, high), (title, colour_name) in RANK_TIERS.items():
        if low <= level <= high:
            colour_id = COLOR_MAP[colour_name]
            return f"\033[48;5;{colour_id}m\033[38;5;16m ✦ {title} ✦ {RESET}"

def update_streak():
    def update(trainer):
        today = date.today()
        today_str = today.isoformat()
        last_date_str = trainer.get('streak', {}).get('last_active_date', today_str)

        try:
            last_date = date.fromisoformat(last_date_str)
        except ValueError:
            last_date = today - timedelta(days=1)

        if last_date < today:
            trainer['daily_catch']['count'] = 0
            trainer['daily_catch']['date'] = today_str

        if last_date == today:
            # First ever catch (or a profile created today) must still count as day 1.
            if trainer['streak']['current'] < 1:
                trainer['streak']['current'] = 1
        elif last_date == today - timedelta(days=1):
            trainer['streak']['current'] += 1
        else:
            trainer['streak']['current'] = 1

        if trainer['streak']['current'] > trainer['streak']['longest']:
            trainer['streak']['longest'] = trainer['streak']['current']

        trainer['streak']['last_active_date'] = today_str
        return trainer

    _locked_trainer_update(update)

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
    import device
    favourites, note = [], ""
    while len(favourites) < 6:
        lines = [f"{YELLOW}Choose 6 favourite Pokemon{RESET}", "",
                 "• Enter a Pokemon name (e.g. Bulbasaur)",
                 f"• Or a National Dex number (1-{max_dex})", "• No duplicates", ""]
        for i, dex in enumerate(favourites, 1):
            name = next(n for d, n in all_pokemon_list if d == dex)
            lines.append(f"  {GREEN}✔{RESET} {i}) #{dex:03d} {name.title()}")
        if note:
            lines += ["", note]
        raw = device.prompt(lines, f"Favourite {len(favourites)+1}/6:", info=SETUP_INFO,
                            slots=("Setup", f"{len(favourites)}/6 chosen")).strip()
        if not raw:
            continue

        dex = None
        key = normalize_name(raw)
        if key in POKEMON_LOOKUP:
            dex = POKEMON_LOOKUP[key]
        elif raw.isdigit():
            dex = int(raw)
            if dex < 1 or dex > max_dex:
                note = f"{RED}✖ Dex number must be between 1 and {max_dex}{RESET}"
                continue
        else:
            note = f"{RED}✖ Unknown Pokemon name{RESET}"
            continue

        if dex in favourites:
            note = f"{RED}✖ Pokemon already selected{RESET}"
            continue

        favourites.append(dex)
        name = next(n for d, n in all_pokemon_list if d == dex)
        note = f"{GREEN}✔ Added #{dex} {name.title()}{RESET}"

    return favourites

# -----------------------
# Trainer Creation
# -----------------------
def create_trainer_profile(editing=False):
    import device
    header = [f"{YELLOW}{'Editing profile' if editing else 'Welcome, Trainer!'}{RESET}", ""]

    name, error = "", ""
    while not (name.isalpha() and len(name) <= 12):
        lines = header + ["What's your name?"] + (["", f"{RED}{error}{RESET}"] if error else [])
        name = device.prompt(lines, "Trainer name (letters only):", info=SETUP_INFO,
                             slots=("Setup", ""), max_len=12).strip().title()
        error = "✖ Letters only, up to 12 of them."

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

    # Merge the profile fields into the *current* file under the lock, so XP,
    # streaks and achievements earned by other terminals while this
    # prompt was open are never overwritten.
    def update(trainer):
        trainer["name"] = name
        trainer["region"] = region
        trainer["hometown"] = hometown
        trainer["favourites"] = favourites
        if not editing:
            trainer["created"] = date.today().isoformat()
        return trainer

    return _locked_trainer_update(update)

# -----------------------
# Load Trainer
# -----------------------
def load_trainer_profile(create_profile=False, update_stats=False):
    try:
        trainer = _normalize_trainer(json.loads(TRAINER_FILE.read_text()))
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
        
    import device
    W = 46
    GREY = "\033[38;5;245m"
    BOLD = "\033[1m"

    def section_profile():
        return [
            f"Name:         {trainer['name']}",
            f"Region:       {trainer['region']}",
            f"Home Town:    {trainer['hometown']}",
            f"Started:      {trainer['created']}",
            "",
            f"Level:        {lvl}",
            f"Progress:     {bar(current_xp, next_lvl_req, width=16)} ",
            f"              {current_xp}/{next_lvl_req} XP",
            f"Lifetime XP:  {lifetime_xp:,}",
            f"Rank:         {rank}",
        ]

    def section_streaks():
        return [
            f"Daily Catch Count:     {daily_catch_count}",
            f"Current Catch Streak:  {streak_current}",
            f"Longest Catch Streak:  {streak_longest}",
            f"Last Catch Date:       {streak_last_date}",
        ]

    def section_completion():
        rows = []
        for gen in sorted(gen_progress):
            caught, total = gen_progress[gen]
            done = caught == total and total > 0
            colour = GREEN if done else YELLOW if caught > 0 else RED
            rows.append(f"Gen {gen}  {bar(caught, total, width=18)} {colour}{caught:>3}/{total}{' ✔' if done else ''}{RESET}")
        return rows + ["", f"{GREY}Completed generations: 1 in 10 shiny.{RESET}"]

    def section_favourites():
        rows = []
        for i, dex in enumerate(favourites, 1):
            name = get_pokemon_name(dex)
            colour = f"\033[48;5;{get_pokemon_color_code(name)}m\033[38;5;16m"
            rows.append(f"{i})  {colour} {name.title().center(15)} {RESET}  #{dex:03d}")
        return rows + ["", f"{GREY}Press 1-{len(favourites)} to see one, normal and shiny.{RESET}"] if rows else \
            [f"{GREY}No favourites yet - press e to choose them.{RESET}"]

    tabs = [("Profile", section_profile), ("Streaks", section_streaks),
            ("Pokedex", section_completion), ("Favourites", section_favourites)]
    tab = 0
    should_redraw = True

    while True:
        if should_redraw:
            title, build = tabs[tab]
            strip = "  ".join(f"{INVERT} {t} {RESET}" if i == tab else f"{GREY}{t}{RESET}"
                              for i, (t, _) in enumerate(tabs))
            lines = [f"{YELLOW}{BOLD}{'TRAINER PROFILE':^{W}}{RESET}", strip,
                     f"{GREY}{'─' * W}{RESET}", ""] + build()
            # The green display: the trainer summary on the Profile tab only.
            info = {
                "Profile": [f"{BOLD}{trainer['name'].upper()}{RESET}", f"Level {lvl}", f"{rank}", "",
                            f"XP {current_xp}/{next_lvl_req}",
                            "█" * int(26 * min(current_xp, next_lvl_req) / max(1, next_lvl_req)), "",
                            f"Streak: {streak_current} day{'s' if streak_current != 1 else ''}"],
                "Streaks": [f"{BOLD}STREAKS{RESET}", "", "Catch at least one", "Pokemon a day to keep",
                            "your streak going."],
                "Pokedex": [f"{BOLD}POKEDEX{RESET}", "", "How much of each", "generation you've caught."],
                "Favourites": [f"{BOLD}FAVOURITES{RESET}", "", "Your six favourite", "Pokemon. Press 1-6 to",
                               "see one, normal and", "shiny."],
            }[title]
            device.show(lines, width=W, info=info,
                        keys=[("←→", "Tab"), ("1-6", "Fav"), ("e", "Edit"), ("b", "Back")],
                        slots=(f"Lv {lvl}", title),
                        lights=(True, streak_current > 0, True), status=trainer["name"],
                        title="Trainer Profile")

        key = device.readkey()
        if key is None:
            should_redraw = True              # window resized: redraw to fit
            continue
        if key in (readchar.key.LEFT, readchar.key.RIGHT):
            tab = (tab + (1 if key == readchar.key.RIGHT else -1)) % len(tabs)
            should_redraw = True
            continue
        key = key.lower()

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
            pokedex.draw_details(name, 1, 1, showcase=True)
            clear()
            should_redraw = True
        else:
            should_redraw = False
            pass