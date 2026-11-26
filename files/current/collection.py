"""Your individual Pokemon: when each was caught, its nature, who caught it,
and every trainer it has belonged to.

pokedex.txt stays the source of truth for *how many* of each Pokemon you have
(it's tamper-checked, and poketerm writes it on every catch). This module keeps
the story of each one in pokemon.log, an append-only list of events:

    caught     a new Pokemon (date and time, nature, catch number, you as trainer)
    legacy     one you had before poketerm 1.0 (no date or place known)
    sent       one that left in a trade (with the trade it left in)
    received   one that arrived in a trade (with its whole history)
    returned   one that came back because a trade was cancelled
    released   one that no longer appears in your Pokedex (e.g. poketerm --remove)
    renamed    you gave one a nickname (or cleared it)
    favourite  you marked one as a favourite (or unmarked it): favourites can't be traded

Replaying the events gives the Pokemon you have now. reconcile() makes that
agree with pokedex.txt: missing ones are added as "legacy" (so Pokemon from
older versions just appear, with unknown dates), and extra ones are released
(legacy ones first). So the details can never drift from the real counts, even
after a crash or a manual repair.
"""
import json
import os
import random
import re
import secrets
from datetime import datetime
from pathlib import Path

APP_DIR = Path.home() / ".local/share/poketerm"
LOG = APP_DIR / "pokemon.log"

NATURES = [
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed", "Impish",
    "Lax", "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild", "Quiet", "Bashful",
    "Rash", "Calm", "Gentle", "Sassy", "Careful", "Quirky",
]
MAX_HISTORY = 20
MAX_NICKNAME = 12
NICKNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'-]{0,11}$")
ID_RE = re.compile(r"^[0-9a-f]{16}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id():
    return secrets.token_hex(8)


def _clean_name(value, limit=16):
    if not isinstance(value, str):
        return "Trainer"
    text = "".join(ch for ch in value if ch.isprintable() and ch not in "\x1b\u009b")
    return re.sub(r"\s+", " ", text).strip()[:limit] or "Trainer"


# ---------------------------------------------------------------------------
# The event log
# ---------------------------------------------------------------------------
def _append(*events):
    """Append events. Each line is far below the size the OS writes atomically,
    so terminals catching at the same time never interleave lines."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(e, separators=(",", ":"), sort_keys=True) + "\n" for e in events)
    fd = os.open(str(LOG), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data.encode())
    finally:
        os.close(fd)


def _events():
    try:
        lines = LOG.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue                    # a torn or damaged line: skip it, keep the rest
        if isinstance(e, dict) and isinstance(e.get("e"), str) and isinstance(e.get("id"), str):
            out.append(e)
    return out


def _replay(events):
    """{id: record} of the Pokemon you have now, in the order you got them."""
    have = {}
    for e in events:
        kind, pid = e["e"], e["id"]
        if kind in ("caught", "legacy", "received", "returned"):
            rec = e.get("pokemon")
            if isinstance(rec, dict):
                have[pid] = dict(rec, id=pid)
        elif kind in ("sent", "released"):
            have.pop(pid, None)
        elif kind == "renamed" and pid in have:
            have[pid]["nickname"] = clean_nickname(e.get("nickname"))
        elif kind == "favourite" and pid in have:
            have[pid]["favourite"] = bool(e.get("on"))
    return have


def clean_nickname(value):
    """A nickname, or None: letters, numbers, spaces and . ' - only, up to 12."""
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value if NICKNAME_RE.match(value) else None


def _counts_from_pokedex():
    counts = {}
    try:
        text = (APP_DIR / "pokedex.txt").read_text()
    except FileNotFoundError:
        return counts
    for line in text.splitlines():
        m = re.match(r"^(\S+)( \(shiny\))? (\d+)$", line.strip())
        if m:
            key = (m.group(1), bool(m.group(2)))
            counts[key] = counts.get(key, 0) + int(m.group(3))
    return counts


# ---------------------------------------------------------------------------
# Recording catches
# ---------------------------------------------------------------------------
def record_catch(name, shiny, trainer, caught_at=None, level=None):
    """Record a new catch (called by poketerm for every Pokemon caught).
    Returns the new Pokemon's record."""
    events = _events()
    caught_before = sum(1 for e in events
                        if e["e"] == "caught" and isinstance(e.get("pokemon"), dict) and e["pokemon"].get("name") == name)
    # Older catches aren't in the log; the Pokedex count (which already
    # includes this catch) tells us how many of this kind you had.
    counts = _counts_from_pokedex()
    had = counts.get((name, False), 0) + counts.get((name, True), 0) - 1
    pid = new_id()
    rec = {
        "name": name, "shiny": bool(shiny), "caught_at": caught_at or now(),
        "nature": random.choice(NATURES), "catch_no": max(caught_before, had) + 1,
        "original_trainer": _clean_name(trainer), "history": [],
        "met_level": level if isinstance(level, int) and not isinstance(level, bool) and 0 <= level <= 1000 else None,
    }
    _append({"e": "caught", "id": pid, "pokemon": rec})
    return dict(rec, id=pid)


# ---------------------------------------------------------------------------
# Reading (and keeping in step with the Pokedex)
# ---------------------------------------------------------------------------
def reconcile():
    """Make the recorded Pokemon match the Pokedex counts and return them all.
    Call with the Pokedex lock held when changing anything else at the same time."""
    events = _events()
    have = _replay(events)
    counts = _counts_from_pokedex()
    by_kind = {}
    for rec in have.values():
        by_kind.setdefault((rec.get("name"), bool(rec.get("shiny"))), []).append(rec)
    fixes = []
    for key in set(counts) | set(by_kind):
        want, recs = counts.get(key, 0), by_kind.get(key, [])
        if len(recs) < want:                              # from before 1.0, or a missed record
            for _ in range(want - len(recs)):
                pid = new_id()
                fixes.append({"e": "legacy", "id": pid, "pokemon": {
                    "name": key[0], "shiny": key[1], "caught_at": None, "nature": None,
                    "catch_no": None, "original_trainer": None, "history": [], "legacy": True}})
        elif len(recs) > want:                            # removed outside poketerm's knowledge
            extra = sorted(recs, key=lambda r: (not r.get("legacy"), bool(r.get("favourite")), r.get("caught_at") or ""))
            for rec in extra[: len(recs) - want]:
                fixes.append({"e": "released", "id": rec["id"], "at": now()})
    if fixes:
        _append(*fixes)
        have = _replay(events + fixes)
    return list(have.values())


def of_kind(name, shiny=None):
    """Your Pokemon of one kind (both normal and shiny unless `shiny` is given),
    oldest catch first, with unknown dates (legacy) first."""
    recs = [r for r in reconcile() if r.get("name") == name and (shiny is None or bool(r.get("shiny")) == shiny)]
    return sorted(recs, key=lambda r: (r.get("caught_at") is not None, r.get("caught_at") or ""))


def get(pid):
    return _replay(_events()).get(pid)


# ---------------------------------------------------------------------------
# Trading (called by trade_store with the Pokedex lock held)
# ---------------------------------------------------------------------------
def choose(pid, name, shiny):
    """The Pokemon that will go in a trade (nothing is changed yet). If `pid`
    isn't one we have (e.g. its record was lost), the oldest of that kind goes."""
    have = _replay(_events())
    rec = have.get(pid) if pid else None
    if rec and rec.get("favourite"):
        raise FavouriteError(f"That {name.replace('-', ' ').title()} is one of your favourites, so it can't be "
                             f"traded. Unmark it in the Pokedex first.")
    if not rec or rec.get("name") != name or bool(rec.get("shiny")) != bool(shiny):
        same = [r for r in have.values() if r.get("name") == name and bool(r.get("shiny")) == bool(shiny)
                and not r.get("favourite")]
        same.sort(key=lambda r: (r.get("caught_at") is not None, r.get("caught_at") or ""))
        rec = same[0] if same else None
        if rec is None and any(r.get("name") == name and bool(r.get("shiny")) == bool(shiny)
                               and r.get("favourite") for r in have.values()):
            raise FavouriteError(f"All your {name.replace('-', ' ').title()} are favourites, so none can be "
                                 f"traded. Unmark one in the Pokedex first.")
    return rec


class FavouriteError(Exception):
    """A trade tried to send a Pokemon marked as a favourite."""


def set_nickname(pid, nickname):
    """Name one of your Pokemon (an empty name clears it). Returns the name kept."""
    clean = clean_nickname(nickname) if nickname else None
    if nickname and not clean:
        raise ValueError("Nicknames can use letters, numbers, spaces and . ' - (up to 12).")
    _append({"e": "renamed", "id": pid, "nickname": clean, "at": now()})
    return clean


def set_favourite(pid, on):
    _append({"e": "favourite", "id": pid, "on": bool(on), "at": now()})


def mark_sent(rec, trade_id, to_trainer):
    """Record that this Pokemon left in a trade."""
    if rec and rec.get("id"):
        _append({"e": "sent", "id": rec["id"], "trade_id": trade_id, "to": _clean_name(to_trainer), "at": now()})


def unsend(rec, trade_id):
    """A trade was cancelled: the Pokemon comes back exactly as it was."""
    if rec and isinstance(rec, dict) and rec.get("id"):
        pokemon = {k: v for k, v in rec.items() if k != "id"}
        _append({"e": "returned", "id": rec["id"], "trade_id": trade_id, "pokemon": pokemon, "at": now()})


def receive(details, name, shiny, trade_id, from_trainer, me):
    """Add a Pokemon received in a trade, keeping its story and adding this trade."""
    rec = clean_details(details, name, shiny) or {
        "name": name, "shiny": bool(shiny), "caught_at": None, "nature": None, "catch_no": None,
        "original_trainer": None, "history": [], "legacy": True}
    rec["history"] = (rec.get("history") or [])[-(MAX_HISTORY - 1):] + [
        {"from": _clean_name(from_trainer), "to": _clean_name(me), "date": now()[:10]}]
    pid = details.get("id") if isinstance(details, dict) and ID_RE.match(str(details.get("id", ""))) else new_id()
    if pid in _replay(_events()):
        pid = new_id()                  # never let an incoming id overwrite one we have
    _append({"e": "received", "id": pid, "trade_id": trade_id, "pokemon": rec, "at": now()})
    return dict(rec, id=pid)


def for_trade(rec):
    """The details of one of your Pokemon to send to the other trainer."""
    if not rec:
        return None
    keep = ("name", "shiny", "caught_at", "nature", "catch_no", "original_trainer", "history", "legacy",
            "nickname", "met_level")
    out = {k: rec.get(k) for k in keep if k in rec}
    out["id"] = rec.get("id")
    return out


def clean_details(details, name, shiny):
    """Details from another trainer, checked field by field. Anything unexpected
    is dropped (never trusted), and it must be the Pokemon being traded."""
    if not isinstance(details, dict):
        return None
    if details.get("name") != name or bool(details.get("shiny")) != bool(shiny) or not NAME_RE.match(name):
        return None
    caught = details.get("caught_at")
    nature = details.get("nature")
    num = details.get("catch_no")
    ot = details.get("original_trainer")
    history = []
    for h in details.get("history") or []:
        if isinstance(h, dict) and DATE_RE.match(str(h.get("date", ""))):
            history.append({"from": _clean_name(h.get("from")), "to": _clean_name(h.get("to")), "date": h["date"]})
    return {
        "name": name, "shiny": bool(shiny),
        "caught_at": caught if isinstance(caught, str) and TIME_RE.match(caught) else None,
        "nature": nature if nature in NATURES else None,
        "catch_no": num if isinstance(num, int) and not isinstance(num, bool) and 0 < num < 10**7 else None,
        "original_trainer": _clean_name(ot) if isinstance(ot, str) and ot.strip() else None,
        "history": history[-MAX_HISTORY:],
        "legacy": bool(details.get("legacy")) and not caught,
        "nickname": clean_nickname(details.get("nickname")),
        "met_level": (details["met_level"] if isinstance(details.get("met_level"), int)
                      and not isinstance(details.get("met_level"), bool) and 0 <= details["met_level"] <= 1000 else None),
    }


# ---------------------------------------------------------------------------
# Words for the Pokedex
# ---------------------------------------------------------------------------
def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def when_text(iso):
    """'25 Nov 2026, 9:41pm' from a stored time."""
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    hour = t.hour % 12 or 12
    return f"{t.day} {t.strftime('%b %Y')}, {hour}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def time_of_day(iso):
    try:
        h = datetime.fromisoformat(iso).hour
    except (TypeError, ValueError):
        return None
    return "morning" if 5 <= h < 12 else "afternoon" if h < 17 else "evening" if h < 21 else "night"


def date_text(iso_date):
    try:
        t = datetime.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return str(iso_date)
    return f"{t.day} {t.strftime('%b %Y')}"


def summary(rec):
    """One line about one Pokemon, e.g. 'Jolly · caught 25 Nov 2026 by Ash'."""
    if not rec:
        return ""
    nick = f'"{rec["nickname"]}" ' if rec.get("nickname") else ""
    heart = "♥ " if rec.get("favourite") else ""
    if rec.get("legacy") and not rec.get("caught_at"):
        return f"{heart}{nick}Caught before poketerm 1.0".replace('" C', '" · c')
    parts = [f"{heart}{nick}".strip()] if (heart or nick) else []
    if rec.get("nature"):
        parts.append(rec["nature"])
    when = rec.get("caught_at")
    if when:
        t = when_text(when)
        parts.append(f"caught {t.split(',')[0]}" if t else "caught")
    if rec.get("original_trainer"):
        parts.append(f"by {rec['original_trainer']}")
    return " · ".join(parts).replace("· by", "by") or "Details unknown"


def display_name(rec, species):
    """'Sparky (Pikachu)' if it has a nickname, else 'Pikachu'."""
    pretty = species.replace("-", " ").title()
    return f"{rec['nickname']} ({pretty})" if rec and rec.get("nickname") else pretty
