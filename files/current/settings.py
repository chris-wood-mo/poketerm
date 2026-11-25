"""poketerm's settings: chosen on the Pokedex's settings page (press s).

Stored in ~/.local/share/poketerm/settings.conf as simple name=value lines so
the poketerm script (zsh) can read them cheaply on every new terminal. Unknown
or invalid values are ignored and the default is used instead, so a hand-edited
file can never stop poketerm working.
"""
import os
import re
import tempfile
from pathlib import Path

FILE = Path.home() / ".local/share/poketerm/settings.conf"
ALL_GENERATIONS = list(range(1, 9))
DEFAULTS = {
    "generations": ALL_GENERATIONS,
    "system_details": True,
    "animations": True,
    "update_check": True,
}
HEADER = ("# poketerm settings. Change them in the Pokedex: open it with `pokedex` and press s.\n"
          "# Changes apply from the next terminal you open.\n")


def load():
    """The current settings (defaults for anything missing or invalid)."""
    values = dict(DEFAULTS)
    try:
        lines = FILE.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return values
    for line in lines:
        key, _, raw = line.partition("=")
        key, raw = key.strip(), raw.strip()
        if key == "generations" and re.fullmatch(r"[1-8](,[1-8])*", raw):
            values["generations"] = sorted({int(g) for g in raw.split(",")})
        elif key in ("system_details", "animations", "update_check") and raw in ("on", "off"):
            values[key] = raw == "on"
    return values


def save(values):
    """Write the settings (atomically, so a crash can't leave half a file)."""
    gens = sorted({g for g in values.get("generations", ALL_GENERATIONS) if g in ALL_GENERATIONS}) or ALL_GENERATIONS
    text = HEADER + "".join([
        f"generations={','.join(str(g) for g in gens)}\n",
        f"system_details={'on' if values.get('system_details', True) else 'off'}\n",
        f"animations={'on' if values.get('animations', True) else 'off'}\n",
        f"update_check={'on' if values.get('update_check', True) else 'off'}\n",
    ])
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(FILE.parent), prefix=".settings.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def animations_on():
    return load()["animations"]
