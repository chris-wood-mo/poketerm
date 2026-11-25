"""Search by name or number, used by the Pokedex (/) and the trade page (/).

Type to filter; results update as you type, best match first:
    exact name or number  >  name starts with it  >  a word in the name starts
    with it (e.g. "mime" -> Mr. Mime)  >  name contains it
Spaces, hyphens, dots and apostrophes are ignored ("mr mime" = "mr-mime" =
"mrmime"), and "#150" or "150" finds National Dex No. 150.
"""
import re

import readchar

RESET = "\033[0m"
BOLD = "\033[1m"
GREY = "\033[38;5;245m"
YELLOW = "\033[33m"
INVERT = "\033[7m"
MAX_QUERY = 20


def squash(text):
    """Lower-case, without spaces or punctuation: what names are compared on."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def rank(entries, query):
    """`entries` matching `query`, best first. Each entry is a dict with at least
    "name" (e.g. "mr-mime") and "no" (National Dex number)."""
    q = query.strip().lower()
    if not q:
        return list(entries)
    digits = q.lstrip("#").strip()
    if digits.isdigit():
        exact = [e for e in entries if e.get("no") == int(digits)]
        prefix = [e for e in entries if e not in exact and str(e.get("no", "")).startswith(digits)]
        return exact + prefix
    sq = squash(q)
    if not sq:
        return []
    scored = []
    for e in entries:
        name = e["name"]
        flat = squash(name)
        words = re.split(r"[-\s.']", name.lower())
        if flat == sq:
            score = 0
        elif flat.startswith(sq):
            score = 1
        elif any(w.startswith(sq) for w in words[1:]):
            score = 2
        elif sq in flat:
            score = 3
        else:
            continue
        scored.append((score, e.get("no", 0), e.get("shiny", False), e))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored]


def search(entries, title, describe, width=44, info=None, slots=("Search", ""), status="Search"):
    """Show the search screen on the device and return the chosen entry, or None.

    entries  - what can be found (dicts with "name" and "no")
    describe - entry -> the line shown for it in the results
    """
    import device
    query, cursor = "", 0
    while True:
        results = rank(entries, query)
        header = [f"{YELLOW}{BOLD}{title:^{width}}{RESET}", "",
                  f"{BOLD}Search:{RESET} {query}{INVERT} {RESET}",
                  f"{GREY}{'─' * width}{RESET}"]
        keys = [("a-z", "Type"), ("↑↓", "Move"), ("⏎", "Go"), ("Esc", "Cancel")]
        room = max(3, min(12, device.fit_rows(width, info or [], keys) - len(header) - 1))
        cursor = max(0, min(cursor, len(results) - 1))
        top = max(0, min(cursor - room // 2, len(results) - room))
        lines = list(header)
        if not query:
            lines.append(f"{GREY}Type a name (e.g. pika) or a number (e.g. 25).{RESET}")
        elif not results:
            lines.append(f"{GREY}No Pokemon match \"{query}\".{RESET}")
        for i, entry in enumerate(results[top: top + room], start=top):
            text = describe(entry)
            if i == cursor:      # the highlight reads as one bar: no colours inside it
                plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
                lines.append(f"{INVERT}> {plain:<{width - 2}}{RESET}")
            else:
                lines.append(f"  {text}")
        if len(results) > top + room:
            lines.append(f"{GREY}  ...and {len(results) - top - room} more{RESET}")
        device.show(lines, width=width, keys=keys, slots=(slots[0], f"{len(results)} found" if query else slots[1]),
                    info=info or [f"{BOLD}SEARCH{RESET}", "", "Type a name or a", "Pokedex number.", "",
                                  "Spaces and capitals", "don't matter."],
                    lights=(True, False, bool(results and query)), status=status, title=title)
        key = device.readkey()
        if key is None:
            continue                                  # window resized: redraw
        if key == "\x1b":
            return None
        if key in (readchar.key.ENTER, "\n"):
            if query and results:
                return results[cursor]
            continue
        if key == readchar.key.UP:
            cursor -= 1
        elif key == readchar.key.DOWN:
            cursor += 1
        elif key in ("\x7f", "\b"):
            query, cursor = query[:-1], 0
        elif key == "\x03":
            raise KeyboardInterrupt
        elif len(key) == 1 and (key.isalnum() or key in " -.'#") and len(query) < MAX_QUERY:
            query, cursor = query + key, 0
