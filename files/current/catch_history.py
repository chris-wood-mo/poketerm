"""The catch history page (press h on the Pokedex list).

Built from the catch times poketerm has recorded since 0.0.8 (pokemon.log):
your first and latest catch, your rarest, your busiest day, and when in the
day you catch ("You're a night-time catcher"). Every catch counts, including
Pokemon you've since traded away; Pokemon received in trades weren't caught by
you, so they don't. Pokemon from before 0.0.8 have no recorded time: they're
counted, but can't be placed.
"""
from collections import Counter
from datetime import datetime

RESET = "\033[0m"
BOLD = "\033[1m"
GREY = "\033[38;5;245m"
YELLOW = "\033[33m"
GOLD = "\033[38;5;178m"
TRACK = "\033[38;5;108m"

PARTS = ["morning", "afternoon", "evening", "night"]       # collection.time_of_day's names
PART_HOURS = {"morning": "5am-12pm", "afternoon": "12-5pm", "evening": "5-9pm", "night": "9pm-5am"}
PART_COLOURS = {"morning": "\033[38;5;214m", "afternoon": "\033[38;5;220m",
                "evening": "\033[38;5;135m", "night": "\033[38;5;33m"}
VERDICTS = {"morning": "You're a morning catcher", "afternoon": "You're an afternoon catcher",
            "evening": "You're an evening catcher", "night": "You're a night-time catcher"}
MIN_FOR_VERDICT = 5           # fewer catches than this says too little about your habits


def pretty(name):
    return name.replace("-", " ").title()


def _special():
    try:
        import achievements
        sc = achievements.SPECIAL_COLLECTIONS
        get = lambda key: {n.lower() for n in sc.get(key, {}).get("required", ())}
        return get("all_legendaries"), get("all_mythicals"), get("all_pseudo_legendaries")
    except Exception:
        return set(), set(), set()


def rarity(p, species_count, special=None):
    """(tier, reason): lower tiers are rarer. Shinies are rarest (1 in 4096),
    then Mythical, Legendary and pseudo-legendary Pokemon, then any you've only
    caught once."""
    legendary, mythical, pseudo = special if special is not None else _special()
    name = p["name"]
    kind = ("Mythical" if name in mythical else "Legendary" if name in legendary
            else "Pseudo-legendary" if name in pseudo else None)
    if p.get("shiny") and kind:
        return 0, f"Shiny and {kind}!"
    if p.get("shiny"):
        return 1, "a rare shiny"
    if kind:
        return {"Mythical": 2, "Legendary": 3, "Pseudo-legendary": 4}[kind], kind
    if species_count[name] == 1:
        return 5, "your only one"
    return 6, f"Caught {species_count[name]} times"


def stats(events, special=None):
    """Everything the page shows, from pokemon.log's events (collection._events())."""
    catches, legacy = [], 0
    for e in events:
        p = e.get("pokemon")
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            continue
        if e["e"] == "legacy":
            legacy += 1
        elif e["e"] == "caught":
            try:
                t = datetime.fromisoformat(p.get("caught_at"))
            except (TypeError, ValueError):
                continue
            catches.append((t, p))
    catches.sort(key=lambda c: c[0])
    out = {"total": len(catches), "before": legacy, "first": None, "latest": None, "rarest": None,
           "busiest": None, "days": 0, "parts": {k: 0 for k in PARTS}, "verdict": None}
    if not catches:
        return out
    out["first"], out["latest"] = catches[0], catches[-1]
    species_count = Counter(p["name"] for _, p in catches)
    # Rarest; on a tie, the one you caught first.
    best = min(range(len(catches)), key=lambda i: (rarity(catches[i][1], species_count, special)[0], i))
    out["rarest"] = (catches[best][0], catches[best][1], rarity(catches[best][1], species_count, special)[1])
    per_day = Counter(t.date() for t, _ in catches)
    top = max(per_day.values())
    out["busiest"] = (min(d for d, n in per_day.items() if n == top), top)
    out["days"] = len(per_day)
    for t, _ in catches:
        h = t.hour
        out["parts"]["morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening" if 17 <= h < 21
                     else "night"] += 1
    if len(catches) >= MIN_FOR_VERDICT:
        most = max(out["parts"].values())
        leaders = [k for k in PARTS if out["parts"][k] == most]
        out["verdict"] = VERDICTS[leaders[0]] if len(leaders) == 1 else "You catch at all hours"
    return out


def _when(t):
    hour = t.hour % 12 or 12
    return f"{t.day} {t.strftime('%b %Y')}, {hour}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def _day(d):
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b %Y')}"


def _name(p):
    text = pretty(p["name"])
    if p.get("shiny"):
        text = f"{GOLD}{text} (shiny){RESET}"
    return text


def _visible(text):
    import re
    return len(re.sub(r"\x1b\[[0-9;]*m", "", text))


def _pair(first, second, width):
    """'first · second' on one line if it fits, else on two (second indented)."""
    joined = f"{first} {GREY}·{RESET} {second}"
    return [joined] if _visible(joined) <= width else [first, f"  {second}"]


def screen(s, width):
    """The page's lines (and its info panel) for stats `s`, at most `width` wide."""
    lines = [f"{YELLOW}{BOLD}{'CATCH HISTORY':^{width}}{RESET}", f"{GREY}{'─' * width}{RESET}"]
    label = lambda text: f"{BOLD}{text}{RESET}"
    grey = lambda text: f"{GREY}{text}{RESET}"
    if not s["total"]:
        lines += ["", "No catch times recorded yet.", "",
                  grey("Every Pokemon you catch from now on is"),
                  grey("added here: open a new terminal to catch one.")]
        if s["before"]:
            lines += ["", grey(f"Your {s['before']} Pokemon from before poketerm 0.0.8"),
                      grey("have no recorded times.")]
        return lines, [f"{BOLD}CATCH HISTORY{RESET}", "", "Nothing recorded", "yet."]
    first_t, first = s["first"]
    latest_t, latest = s["latest"]
    rare_t, rare, why = s["rarest"]
    day, on_day = s["busiest"]
    lines.append(f"{label('Catches')}  {s['total']} on {s['days']} day{'s' if s['days'] != 1 else ''}")
    if s["before"]:
        long = f"  + {s['before']} from before poketerm 0.0.8 (no times)"
        lines.append(grey(long if len(long) <= width else f"  + {s['before']} from before 0.0.8"))
    lines += ["",
              label("First catch"), f"  {_name(first)}", grey(f"  {_when(first_t)}"),
              label("Latest catch"), f"  {_name(latest)}", grey(f"  {_when(latest_t)}"),
              label("Rarest catch")]
    lines += _pair(f"  {_name(rare)}", grey(why), width) + [grey(f"  {_when(rare_t)}")]
    lines.append(label("Busiest day"))
    lines += _pair(f"  {_day(day)}", f"{on_day} catch{'es' if on_day != 1 else ''}", width)
    lines += ["", label("When you catch")]
    most = max(s["parts"].values()) or 1
    digits = len(str(most))
    bar_room = max(4, min(16, width - 2 - 10 - 1 - digits))
    for part in PARTS:
        n = s["parts"][part]
        filled = round(n / most * bar_room) if n else 0
        bar = f"{PART_COLOURS[part]}{'▆' * filled}{TRACK}{'▆' * (bar_room - filled)}{RESET}"
        lines.append(f"  {part.title():<10}{bar} {n}")
    lines.append("")
    import textwrap
    if s["verdict"]:
        lines += [f"{YELLOW}{BOLD}{t}{RESET}" for t in textwrap.wrap(f"{s['verdict']}!", width)]
    else:
        lines += [grey(t) for t in textwrap.wrap(
            f"Catch {MIN_FOR_VERDICT - s['total']} more to find out when you catch best.", width)]
    info = [f"{BOLD}WHEN YOU CATCH{RESET}", ""] + [f"{p.title():<10}{PART_HOURS[p]}" for p in PARTS] + [
        "", "Rarest: shiny first,", "then Mythical,", "Legendary and", "pseudo-legendary."]
    return lines, info


def history_page():
    import readchar
    import collection
    import device
    s = stats(collection._events())
    while True:
        width = max(24, min(46, device.available_width()))
        lines, info = screen(s, width)
        device.show(lines, width=width, info=info, keys=[("b", "Back")],
                    slots=("History", f"{s['total']} caught"), lights=(True, False, bool(s["total"])),
                    status="History", title="Catch history")
        key = device.readkey()
        if key is None:
            continue                         # window resized: redraw
        if key == "\x03":
            raise KeyboardInterrupt
        if key in ("b", "q", "h", "\x1b", readchar.key.ENTER, "\n"):
            return
