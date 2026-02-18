import os
import sys
import json
import re
import readchar
from pathlib import Path
from datetime import date

POKEDEX_FILE = Path.home() / ".local/share/poketerm/pokedex.txt"
GEN_DIR = Path.home() / ".local/share/poketerm/gen_files"
TRAINER_FILE = Path.home() / ".local/share/poketerm/trainer.json"
RESET = "\033[0m"
BOLD = "\033[1m"
INVERT = "\033[7m"
RED = "\033[38;5;196m"
GREEN = "\033[38;5;46m"
YELLOW = "\033[38;5;226m"
CYAN = "\033[38;5;51m"
MAGENTA = "\033[38;5;201m"
ORANGE = "\033[38;5;208m"
LAVENDER = "\033[38;5;183m"
WIDTH = 85

gen_regions = {
    "gen1": "Kanto",
    "gen2": "Johto",
    "gen3": "Hoenn",
    "gen4": "Sinnoh",
    "gen5": "Unova",
    "gen6": "Kalos",
    "gen7": "Alola",
    "gen8": "Galar",
    "gen9": "Paldea"
}

SPECIAL_COLLECTIONS = {
    "all_legendaries": {
        "name": "Legendary Conqueror",
        "event": "Caught All Legendary Pokemon",
        "category": "special",
        "required": {
            # Gen 1
            "articuno", "zapdos", "moltres", "mewtwo",
            # Gen 2
            "raikou", "entei", "suicune", "lugia", "ho-oh", "celebi",
            # Gen 3
            "regirock", "regice", "registeel", "latias", "latios", 
            "kyogre", "groudon", "rayquaza", "jirachi", "deoxys",
            # Gen 4
            "uxie", "mesprit", "azelf", "dialga", "palkia", "heatran", 
            "regigigas", "giratina", "cresselia", "phione", "manaphy", 
            "darkrai", "shaymin", "arceus",
            # Gen 5
            "victini", "cobalion", "terrakion", "virizion", "tornadus", 
            "thundurus", "reshiram", "zekrom", "landorus", "kyurem", 
            "keldeo", "meloetta", "genesect",
            # Gen 6
            "xerneas", "yveltal", "zygarde", "diancie", "hoopa", "volcanion",
            # Gen 7
            "type-null", "silvally", "tapu-koko", "tapu-lele", "tapu-bulu", 
            "tapu-fini", "cosmog", "cosmoem", "solgaleo", "lunala", 
            "necrozma", "magearna", "marshadow", "zeraora", "meltan", "melmetal",
            # Gen 8 (Including Hisui)
            "zacian", "zamazenta", "eternatus", "kubfu", "urshifu", 
            "zarude", "regieleki", "regidrago", "glastrier", "spectrier", 
            "calyrex", "enamorus"
        }
    },

    "all_mythicals": {
        "name": "Myth Hunter",
        "event": "Caught All Mythical Pokemon",
        "category": "special",
        "required": {
            # Gen 1
            "mew",
            # Gen 2
            "celebi",
            # Gen 3
            "jirachi", "deoxys",
            # Gen 4
            "phione", "manaphy", "darkrai", "shaymin", "arceus",
            # Gen 5
            "victini", "keldeo", "meloetta", "genesect",
            # Gen 6
            "diancie", "hoopa", "volcanion",
            # Gen 7
            "magearna", "marshadow", "zeraora", "meltan", "melmetal",
            # Gen 8
            "zarude"
        }
    },

    "kanto_birds": {
        "name": "Legendary Trio Master - Kanto Birds",
        "event": "Completed the Legendary Birds",
        "category": "special",
        "required": {"articuno", "zapdos", "moltres"}
    },

    "weather_trio": {
        "name": "Weather Dominator",
        "event": "Completed the Weather Trio",
        "category": "pokedex",
        "required": {"kyogre", "groudon", "rayquaza"}
    },

    "all_starters": {
        "name": "Starter Supreme",
        "event": "Caught All Starter Pokemon",
        "category": "special",
        "required": {
            # Gen 1
            "bulbasaur", "charmander", "squirtle",
            # Gen 2
            "chikorita", "cyndaquil", "totodile",
            # Gen 3
            "treecko", "torchic", "mudkip",
            # Gen 4
            "turtwig", "chimchar", "piplup",
            # Gen 5
            "snivy", "tepig", "oshawott",
            # Gen 6
            "chespin", "fennekin", "froakie",
            # Gen 7
            "rowlet", "litten", "popplio",
            # Gen 8
            "grookey", "scorbunny", "sobble"
        }
    },

    "all_pseudo_legendaries": {
        "name": "Pseudo-Legend Slayer",
        "event": "Caught All Pseudo-Legendaries",
        "category": "special",
        "required": {
            # Gen 1
            "dragonite",
            # Gen 2
            "tyranitar",
            # Gen 3
            "salamence", "metagross",
            # Gen 4
            "garchomp",
            # Gen 5
            "hydreigon",
            # Gen 6
            "goodra",
            # Gen 7
            "kommo-o",
            # Gen 8
            "dragapult"
        }
    },

    "all_eeveelutions": {
        "name": "Eeveelution Enthusiast",
        "event": "Caught All Eeveelutions",
        "category": "special",
        "required": {
            "eevee",          # The Base
            "vaporeon",       # Water (Gen 1)
            "jolteon",        # Electric (Gen 1)
            "flareon",        # Fire (Gen 1)
            "espeon",         # Psychic (Gen 2)
            "umbreon",        # Dark (Gen 2)
            "leafeon",        # Grass (Gen 4)
            "glaceon",        # Ice (Gen 4)
            "sylveon"         # Fairy (Gen 6)
        }
    },

    "all_fossils": {
        "name": "Paleontologist",
        "event": "Caught All Fossil Pokemon",
        "category": "special",
        "required": {
            # Gen 1 (Helix, Dome, Old Amber)
            "omanyte", "kabuto", "aerodactyl",
            
            # Gen 2 (None - No fossils introduced in Johto)
            
            # Gen 3 (Root, Claw)
            "lileep", "anorith",
            
            # Gen 4 (Skull, Armor)
            "cranidos", "shieldon",
            
            # Gen 5 (Cover, Plume)
            "tirtouga", "archen",
            
            # Gen 6 (Jaw, Sail)
            "tyrunt", "amaura",
            
            # Gen 7 (None - Standard fossils weren't introduced)

            # Gen 8 (Fossilized Bird, Fish, Drake, Dino)
            # Note: These are the unique "Chimera" fossils
            "dracozolt", "arctozolt", "dracovish", "arctovish"
        }
    }
}

def clear():
    sys.stdout.write("\033[2J\033[H\033[3J")
    sys.stdout.flush()

# -----------------------------
# Utility
# -----------------------------

def load_trainer():
    if TRAINER_FILE.exists():
        return json.loads(TRAINER_FILE.read_text())
    return {"hall_of_fame": []}


def save_trainer(data):
    TRAINER_FILE.write_text(json.dumps(data, indent=2))


def unlock(trainer, name, event, category):
    hall = trainer.setdefault("hall_of_fame", [])

    unlocked_names = {entry["name"] for entry in hall}

    if name in unlocked_names:
        return False

    hall.append({
        "category": category,
        "date": str(date.today()),
        "name": name,
        "event": event
    })

    return True

def get_generation_species(gen_file):
    return set(gen_file.read_text().splitlines())

def analyze_pokedex():
    if not POKEDEX_FILE.exists():
        return set(), 0, 0, 0

    unique_species = set()
    total_shinies = 0
    highest_catch = 0
    total_pokemon_caught = 0

    for line in POKEDEX_FILE.read_text().splitlines():
        if not line.strip():
            continue

        match = re.search(r"(\d+)$", line)
        count = int(match.group(1)) if match else 1

        total_pokemon_caught += count

        if count > highest_catch:
            highest_catch = count

        if "(shiny)" in line:
            total_shinies += count

        name = re.sub(r"\s+\d+$", "", line)
        name = re.sub(r"\s+\(shiny\)$", "", name)
        unique_species.add(name.strip().lower())

    return unique_species, total_shinies, highest_catch, total_pokemon_caught


def update_daily_catch():
    trainer = load_trainer()
    today_str = str(date.today())
    daily = trainer.get("daily_catch", {"date": today_str, "count": 0})

    if daily.get("date") != today_str:
        daily = {"date": today_str, "count": 1}
    else:
        daily["count"] += 1

    trainer["daily_catch"] = daily
    
    save_trainer(trainer)

# -----------------------------
# Main Checker
# -----------------------------

def check_pokedex_achievements():
    trainer = load_trainer()
    unique_species, total_shinies, highest_catch, total_pokemon_caught = analyze_pokedex()
    total_unique = len(unique_species)
    lvl = trainer.get('level')
    total_xp = trainer.get('total_xp')
    streak_data = trainer.get("streak", {})
    longest = streak_data.get("longest", 0)
    daily_catch = trainer.get("daily_catch", {})
    daily_catch_count = daily_catch.get('count', 0)
    all_achievements = True

    # -----------------
    # Count Milestones
    # -----------------
    if total_unique >= 1:
        unlock(trainer, "Just getting started", "Caught your first Pokemon", "pokedex")
    else:
        all_achievements = False

    if total_unique >= 50:
        unlock(trainer, "You're getting the hang of this!", "Caught 50 Pokemon", "pokedex")
    else:
        all_achievements = False
    
    if total_unique >= 100:
        unlock(trainer, "A true pro", "Caught 100 Pokemon", "pokedex")
    else:
        all_achievements = False

    if total_unique >= 905:
        unlock(trainer, "You sure can open a terminal!", "Caught all 905 Pokemon", "pokedex")
    else:
        all_achievements = False

    if total_pokemon_caught >= 10000:
        unlock(trainer, "How many terminals?", "Caught 10000 Pokemon", "pokedex")
    else:
        all_achievements = False

    # -----------------
    # Daily Streaks
    # -----------------

    if longest >= 3:
        unlock(trainer, "Warming Up", "3 Day Catch Streak", "catch")
    else:
        all_achievements = False

    if longest >= 7:
        unlock(trainer, "Weeklong Catcher", "7 Day Catch Streak", "catch")
    else:
        all_achievements = False
        
    if longest >= 30:
        unlock(trainer, "No longer casual thing", "30 Day Catch Streak", "catch")
    else:
        all_achievements = False

    if longest >= 100:
        unlock(trainer, "At this point, the Pokemon fear you", "100 Day Catch Streak", "catch")
    else:
        all_achievements = False

    if longest >= 365:
        unlock(trainer, "You have not missed a single day", "365 Day Catch Streak", "catch")
    else:
        all_achievements = False

    # -----------------
    # Shiny Milestones
    # -----------------

    if total_shinies >= 1:
        unlock(trainer, "Oooo a Shiny!", "Caught your first Shiny Pokemon", "catch")
    else:
        all_achievements = False

    if total_shinies >= 5:
        unlock(trainer, "You're really lucky!", "Caught 5 Shiny Pokemon", "catch")
    else:
        all_achievements = False

    if total_shinies >= 100:
        unlock(trainer, "Is this even luck anymore", "Caught 100 Shiny Pokemon", "catch")
    else:
        all_achievements = False
        
    # -----------------
    # Daily Catch Milestones
    # -----------------

    if daily_catch_count >= 5:
        unlock(trainer, "You can almost make a full team!", "Caught 5 Pokemon in a day", "catch")
    else:
        all_achievements = False

    if daily_catch_count >= 10:
        unlock(trainer, "You must like opening terminals", "Caught 10 Pokemon in a day", "catch")
    else:
        all_achievements = False

    if daily_catch_count >= 50:
        unlock(trainer, "Time to log off now!", "Caught 50 Pokemon in a day", "catch")
    else:
        all_achievements = False
    
    # -----------------
    # Highest Catch Milestones
    # -----------------
    if highest_catch >= 10:
        unlock(trainer, "I hope its not a weedle again!", "Caught a Pokemon 10 times", "catch")
    else:
        all_achievements = False
    
    if highest_catch >= 25:
        unlock(trainer, "Not another one!", "Caught a Pokemon 25 times", "catch")
    else:
        all_achievements = False
        
    # -----------------
    # Level Milestones
    # -----------------

    if lvl >= 10:
        unlock(trainer, "Apprentice Trainer", "Reach level 10", "rank")
    else:
        all_achievements = False

    if lvl >= 25:
        unlock(trainer, "Veteran Trainer", "Reach level 25", "rank")
    else:
        all_achievements = False

    if lvl >= 50:
        unlock(trainer, "Master Trainer", "Reach level 50", "rank")
    else:
        all_achievements = False

    if total_xp >= 1000:
        unlock(trainer, "XP Initiate - The grind has begun", "Reach a total XP of 1000", "rank")
    else:
        all_achievements = False

    if total_xp >= 10000:
        unlock(trainer, "XP Adept - Momentum is undeniable", "Reach a total XP of 10000", "rank")
    else:
        all_achievements = False

    if total_xp >= 50000:
        unlock(trainer, "XP Elite - You’re operating at scale", "Reach a total XP of 50000", "rank")
    else:
        all_achievements = False

    if total_xp >= 100000:
        unlock(trainer, "XP Grandmaster - The numbers fear you", "Reach a total XP of 100000", "rank")
    else:
        all_achievements = False

    # -----------------
    # Generation Completion
    # -----------------
    all_complete = True

    for gen_file in sorted(GEN_DIR.glob("gen*_list.txt")):
        gen_species = get_generation_species(gen_file)
        if gen_species.issubset(unique_species):
            region = gen_regions[gen_file.stem.replace('_list','')]
            generation_number = gen_file.stem.replace('_list','').replace('gen', '')
            unlock(
                trainer,
                f"{region} Master",
                f"Completed Generation {generation_number}",
                "pokedex"
            )
        else:
            all_complete = False
            all_achievements = False

    if all_complete:
        unlock(trainer, "Lets collect some shinys now!", "Completed all Generations", "pokedex")

    # -----------------
    # Pokemon specific collection
    # -----------------
    for collection in SPECIAL_COLLECTIONS.values():
        required = {name.lower() for name in collection["required"] }
        if required.issubset(unique_species):
            unlock(
                trainer,
                collection["name"],
                collection["event"],
                collection["category"]
            )
        else:
            all_achievements = False

    # -----------------
    # Hall of Fame Induction
    # -----------------
    if all_achievements:
        unlock(trainer, "Poketerm - Completed it mate!", "Completed all achievements", "special")
    
    save_trainer(trainer)

def get_visible_len(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-v])')
    stripped = ansi_escape.sub('', text)

    count = 0
    for char in stripped:
        count += 2 if ord(char) > 10000 else 1
    return count

def line(text=""):
    visible_width = get_visible_len(text)
    padding = WIDTH - visible_width
    return f"{MAGENTA}║{RESET} {text}{' ' * padding} {MAGENTA}║{RESET}"

def dashed_line():
    return f"{MAGENTA}╟{RESET}{'─' * (WIDTH + 2)}{MAGENTA}╢{RESET}"

def centered_line(text="", color=MAGENTA):
    vis_len = get_visible_len(text)
    
    total_padding = WIDTH - vis_len
    
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding
    
    return f"{MAGENTA}║{RESET} {' ' * left_padding}{text}{' ' * right_padding} {MAGENTA}║{RESET}"

def display_hall_of_fame():
    check_pokedex_achievements()
    trainer = load_trainer()
    hall = trainer.get("hall_of_fame", [])

    if hall == []:
        clear()
        print(f"{RED}Catch some Pokemon to earn some achievements!{RESET}")
        print("\nPress any key to return…")
        readchar.readkey()
        clear()
    
    should_redraw = True

    while True:
        if should_redraw:
            clear()
            os.system("clear" if os.name == "posix" else "cls")
        
            print(f"{MAGENTA}╔" + "═" * (WIDTH + 2) + f"╗{RESET}")
            print(centered_line(f"{INVERT}  🌟 HALL OF FAME 🌟  {RESET}"))
            
            if not hall:
                print(line())
                print(centered_line("No achievements yet."))
                print(f"{MAGENTA}╚" + "═" * (WIDTH + 2) + f"╝{RESET}")
                return

            categories = {
                "pokedex": {"name": "Pokedex Milestones", "emoji": "🏆", "color": LAVENDER},
                "catch": {"name": "Catch Achievements", "emoji": "🔥", "color": YELLOW},
                "rank": {"name": "Level Milestones", "emoji": "🎓", "color": GREEN},
                "special": {"name": "Special Collections", "emoji": "🔹", "color": CYAN},
            }

            other_achievements = [a for a in hall if "Hall of Fame" not in a["name"]]
            
            col_width = 40 

            for cat_key, cat_info in categories.items():
                cat_achievements = [a for a in other_achievements if a["category"] == cat_key]
                if not cat_achievements:
                    continue

                print(dashed_line())
                print(centered_line(f"{INVERT}  {cat_info['emoji']} {cat_info['name']} {cat_info['emoji']}  {RESET}"))
                print(line())

                for entry in cat_achievements:
                    name_part = f"{cat_info['emoji']} {entry['name']}"
                    desc_part = f"{entry['event']} ({entry['date']})"
                    
                    vis_name = get_visible_len(name_part)
                    name_padding = " " * (col_width - vis_name)
                    
                    full_text = f"{cat_info['color']}{name_part}{name_padding} {desc_part}{RESET}"
                    print(line(full_text))
            
            print(dashed_line())
            print(line("[B] Back"))
            print(f"{MAGENTA}╚" + "═" * (WIDTH + 2) + f"╝{RESET}")

        key = readchar.readkey().lower()

        if key == "b": 
            should_redraw = True
            break
        else:
            should_redraw = False
            pass