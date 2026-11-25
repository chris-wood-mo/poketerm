"""The trading screens in the Pokedex (press `t`), and the trade animations."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import readchar
import trade_lan as lan
import trade_protocol as proto
import trade_store as store

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
WHITE = "\033[97m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"
INVERT = "\033[7m"

WIDTH = 58                  # inside the frame
PAGE_SIZE = 10
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GO_COLORSCRIPTS = Path.home() / ".local/share/pokemon-go-colorscripts/pokemon-go-colorscripts"
CACHE_DIR = Path.home() / ".local/share/poketerm/cache/sprite_data"
TRAINER_FILE = Path.home() / ".local/share/poketerm/trainer.json"


# ---------------------------------------------------------------------------
# Drawing helpers (same frame style as the rest of the Pokedex)
# ---------------------------------------------------------------------------
def visible(text):
    return len(ANSI_RE.sub("", text))


def fit(text, width):
    """Cut `text` (which may contain colours) to `width` visible characters."""
    out, n, i = [], 0, 0
    while i < len(text) and n < width:
        m = ANSI_RE.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(text[i])
        n += 1
        i += 1
    return "".join(out) + (RESET if out else "")


class Screen:
    def __init__(self):
        self.lines = []

    def top(self):
        self.lines.append(f"{RED}┌{'─' * (WIDTH + 2)}┐{RESET}")

    def sep(self):
        self.lines.append(f"{RED}├{'─' * (WIDTH + 2)}┤{RESET}")

    def bottom(self):
        self.lines.append(f"{RED}└{'─' * (WIDTH + 2)}┘{RESET}")

    def row(self, text=""):
        text = fit(text, WIDTH)
        self.lines.append(f"{RED}│{RESET} {text}{' ' * (WIDTH - visible(text))} {RED}│{RESET}")

    def center(self, text):
        pad = max(0, (WIDTH - visible(text)) // 2)
        self.row(" " * pad + text)

    def wrap(self, text, colour=""):
        words, line = text.split(), ""
        for w in words:
            if len(line) + len(w) + 1 > WIDTH:
                self.row(colour + line + (RESET if colour else ""))
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            self.row(colour + line + (RESET if colour else ""))

    def show(self):
        sys.stdout.write("\033[H" + "\n".join(self.lines) + "\n\033[J")
        sys.stdout.flush()


def pokemon_label(p, width=None):
    label = store.display(p).replace(" (shiny)", "")
    star = f" {YELLOW}★ shiny{RESET}" if p.get("shiny") else ""
    return label + star


def read_trainer():
    try:
        data = json.loads(TRAINER_FILE.read_text())
        return {"name": str(data.get("name") or "Trainer"), "level": int(data.get("level") or 0)}
    except (OSError, ValueError, TypeError):
        return {"name": "Trainer", "level": 0}


# ---------------------------------------------------------------------------
# Sprites and animations
# ---------------------------------------------------------------------------
def colorscripts_cmd():
    if os.access(GO_COLORSCRIPTS, os.X_OK):
        return str(GO_COLORSCRIPTS)
    return shutil.which("pokemon-go-colorscripts") or shutil.which("pokemon-colorscripts")


def get_sprite(pokemon):
    """Sprite lines for a Pokemon, exactly as a new terminal shows them
    (straight from colorscripts), trimmed so they centre properly."""
    name, shiny = pokemon["name"], bool(pokemon.get("shiny"))
    lines = []
    cmd = colorscripts_cmd()
    if cmd:
        try:
            args = [cmd, "--no-title", "-n", name] + (["-s"] if shiny else [])
            lines = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            lines = []
    if not lines:
        cache = CACHE_DIR / f"{name}_{'shiny' if shiny else 'normal'}.txt"
        try:
            lines = cache.read_text().splitlines()
        except OSError:
            return []
    plain = lambda l: ANSI_RE.sub("", l)
    while lines and not plain(lines[-1]).strip():
        lines.pop()
    while lines and not plain(lines[0]).strip():
        lines.pop(0)
    indent = min((len(plain(l)) - len(plain(l).lstrip(" ")) for l in lines if plain(l).strip()), default=0)
    return [_drop_columns(l, indent) for l in lines]


def _drop_columns(line, n):
    """Remove the first n visible (space) columns, keeping colour codes."""
    out, i = [], 0
    while i < len(line):
        m = ANSI_RE.match(line, i)
        if m:
            out.append(m.group())
            i = m.end()
        elif n > 0 and line[i] == " ":
            n -= 1
            i += 1
        else:
            out.append(line[i:])
            break
    return "".join(out)


BALL = [
    (RED, "  ▄█████▄  "),
    (RED, " █████████ "),
    (GREY, " ━━━━" + WHITE + "◉" + GREY + "━━━━ "),
    (WHITE, " █████████ "),
    (WHITE, "  ▀█████▀  "),
]
BALL_W, BALL_H = 11, len(BALL)


class Animator:
    """Frame-by-frame drawing at fixed positions. Any key skips ahead."""

    def __init__(self):
        size = shutil.get_terminal_size((80, 24))
        self.cols, self.rows = size.columns, size.lines
        self.skip = False

    def pause(self, seconds):
        if self.skip:
            return
        if readchar.readkey_timeout(seconds) is not None:
            self.skip = True

    def at(self, row, col, text):
        if 1 <= row <= self.rows:
            sys.stdout.write(f"\033[{row};{max(1, col)}H{text}{RESET}")

    def clear(self):
        sys.stdout.write("\033[2J\033[H")

    def caption(self, row, text):
        self.at(row, 1, "\033[2K")
        self.at(row, max(1, (self.cols - visible(text)) // 2 + 1), text)
        sys.stdout.flush()

    def draw_ball(self, row, col, lift=0):
        for i, (colour, line) in enumerate(BALL):
            r = row + i - (lift if i < 2 else 0)
            self.at(r, col, colour + line)

    def erase(self, row, col, width, height):
        for i in range(height):
            self.at(row + i, col, " " * width)

    def sprite_box(self, sprite):
        width = max((visible(l) for l in sprite), default=0)
        height = min(len(sprite), max(1, self.rows - 6))
        top = max(2, (self.rows - height) // 2 - 1)
        left = max(1, (self.cols - width) // 2 + 1)
        return top, left, width, height

    def draw_sprite(self, sprite, top, left, rows=None):
        for i, line in enumerate(sprite[: rows if rows is not None else len(sprite)]):
            if top + i >= self.rows - 2:
                break
            self.at(top + i, left, line)


def animate_send(pokemon, peer_name):
    """Your Pokemon goes back into its Poke Ball and flies off to the other trainer."""
    a = Animator()
    sprite = get_sprite(pokemon)
    name = store.display(pokemon).replace(" (shiny)", "")
    a.clear()
    top, left, width, height = a.sprite_box(sprite or [" "])
    caption_row = min(a.rows - 1, top + height + 1)
    a.draw_sprite(sprite, top, left)
    a.caption(caption_row, f"{YELLOW}Take care, {name}!{RESET}")
    a.pause(1.0)
    a.caption(caption_row, f"{YELLOW}{name}, return!{RESET}")
    for shown in range(min(height, len(sprite)), -1, -max(1, height // 8)):
        a.erase(top, left, width, height)
        a.draw_sprite(sprite, top + (height - shown) // 2, left, shown)
        sys.stdout.flush()
        a.pause(0.04)
    a.erase(top, left, width, height)
    ball_row = top + max(0, height // 2 - BALL_H // 2)
    col = (a.cols - BALL_W) // 2 + 1
    a.draw_ball(ball_row, col)
    sys.stdout.flush()
    a.pause(0.4)
    while col < a.cols:
        a.erase(ball_row - 1, col, BALL_W, BALL_H + 1)
        col += 3
        hop = 1 if (col // 3) % 4 in (1, 2) else 0
        if col + BALL_W <= a.cols:
            a.draw_ball(ball_row - hop, col)
        sys.stdout.flush()
        a.pause(0.03)
    a.caption(caption_row, f"{GREEN}{name} was sent to {peer_name}.{RESET}")
    a.pause(1.0)


def animate_receive(pokemon, peer_name):
    """A Poke Ball arrives, wobbles, opens, and your new Pokemon appears."""
    a = Animator()
    sprite = get_sprite(pokemon)
    name = store.display(pokemon).replace(" (shiny)", "")
    a.clear()
    top, left, width, height = a.sprite_box(sprite or [" "])
    caption_row = min(a.rows - 1, top + height + 1)
    ball_row = top + max(0, height // 2 - BALL_H // 2)
    target = (a.cols - BALL_W) // 2 + 1
    a.caption(caption_row, f"{YELLOW}{peer_name} sent you a Poke Ball!{RESET}")
    col = 1
    while col < target:
        a.erase(ball_row - 1, max(1, col - 3), BALL_W + 3, BALL_H + 1)
        a.draw_ball(ball_row - (1 if (col // 3) % 4 in (1, 2) else 0), col)
        sys.stdout.flush()
        col += 3
        a.pause(0.03)
    a.erase(ball_row - 1, 1, a.cols, BALL_H + 1)
    a.draw_ball(ball_row, target)
    sys.stdout.flush()
    a.pause(0.4)
    for shift in (-1, 1, -1, 1, 0):          # wobble
        a.erase(ball_row, target - 1, BALL_W + 2, BALL_H)
        a.draw_ball(ball_row, target + shift)
        sys.stdout.flush()
        a.pause(0.12 if shift else 0.35)
    a.erase(ball_row - 2, target - 1, BALL_W + 2, BALL_H + 2)
    a.draw_ball(ball_row, target, lift=2)    # the ball opens
    a.at(ball_row - 3, target + BALL_W // 2, WHITE + BOLD + "✦")
    sys.stdout.flush()
    a.pause(0.3)
    a.erase(ball_row - 3, target - 1, BALL_W + 2, BALL_H + 3)
    for shown in range(0, min(height, len(sprite)) + 1, max(1, height // 10)):
        a.draw_sprite(sprite, top, left, shown)
        sys.stdout.flush()
        a.pause(0.03)
    a.draw_sprite(sprite, top, left)
    shiny = f"  {YELLOW}★ It's shiny!{RESET}" if pokemon.get("shiny") else ""
    a.caption(caption_row, f"{GREEN}{peer_name} sent you {name}!{RESET}{shiny}")
    sys.stdout.flush()
    a.skip = False
    a.pause(1.5)


# ---------------------------------------------------------------------------
# The trade page
# ---------------------------------------------------------------------------
class TradePage:
    def __init__(self):
        self.identity = None
        self.listener = None
        self.discovery = None
        self.session = None
        self.incoming = queue.Queue()        # channels for new trades
        self.notices = queue.Queue()         # text to flash in the status line
        self.items = []
        self.cursor = 0
        self.view = "list"                   # list | trainers | address | session
        self.peer_cursor = 0
        self.address = ""
        self.status = ""
        self.status_colour = GREEN
        self.stop = threading.Event()
        self.resolving = set()
        self.trainer = read_trainer()
        self.address_text = ""

    # -- setup / teardown ---------------------------------------------------
    def open(self):
        problem = store.check_requirements()
        if problem:
            return problem
        s = Screen()
        s.top(); s.center(f"{YELLOW}Pokemon Trading{RESET}"); s.sep()
        s.row("Getting ready to trade..."); s.bottom(); s.show()
        try:
            self.identity = store.load_identity()
            store.recover()
            self.refresh_items()
        except store.TradeError as e:
            return str(e)
        try:
            self.listener = lan.Listener(self.identity, self.on_channel)
            self.discovery = lan.Discovery(self.identity, self.trainer["name"], self.listener.port)
            self.address_text = f"{lan.local_address()}:{self.listener.port}"
        except OSError as e:
            self.close()
            return f"Couldn't start trading on this network ({e.strerror or e})."
        threading.Thread(target=self.resolver_loop, daemon=True).start()
        return None

    def close(self):
        self.stop.set()
        if self.session:
            self.session.cancel()
        for part in (self.discovery, self.listener):
            if part:
                part.close()
        while not self.incoming.empty():
            try:
                self.incoming.get_nowait().close()
            except (queue.Empty, OSError):
                pass

    def refresh_items(self):
        self.items = store.tradeable_pokemon()
        self.cursor = max(0, min(self.cursor, len(self.items) - 1))

    def flash(self, text, colour=GREEN):
        self.status, self.status_colour = text, colour

    # -- network callbacks (background threads) ------------------------------
    def on_channel(self, channel):
        try:
            hello, validator = proto.read_first_hello(channel)
        except (proto.ProtocolError, OSError):
            channel.close()
            return
        if hello["purpose"] == "resolve":
            self.run_resolve(channel, hello, validator)
            return
        self.incoming.put((channel, hello, validator))

    def run_resolve(self, channel, hello=None, validator=None):
        try:
            outcomes = proto.resolve_session(channel, self.identity, self.trainer["name"],
                                             first_hello=hello, validator=validator)
            for tid, outcome in outcomes:
                self.notices.put(("Your unfinished trade has now finished." if outcome == "committed"
                                  else "Your unfinished trade was cancelled - your Pokemon is back.",
                                  GREEN if outcome == "committed" else YELLOW))
        except (proto.ProtocolError, store.TradeError, OSError):
            pass
        finally:
            channel.close()

    def resolver_loop(self):
        """Settle pending trades whenever their other trainer is nearby."""
        while not self.stop.wait(3):
            try:
                pending = store.pending_trades()
            except store.TradeError:
                continue
            if not pending:
                continue
            wanted = {p["peer"]["fingerprint"] for p in pending}
            for peer in self.discovery.nearby():
                fp = peer["fp"]
                if fp not in wanted or fp in self.resolving:
                    continue
                self.resolving.add(fp)
                try:
                    ch = lan.connect(self.identity, peer["host"], peer["port"], fp, timeout=5)
                    self.run_resolve(ch)
                except (OSError, lan.ChannelClosed):
                    pass
                finally:
                    self.resolving.discard(fp)

    # -- main loop -------------------------------------------------------------
    def run(self):
        # Key-at-a-time mode from the start, so keys pressed while the page
        # is getting ready aren't lost or echoed onto the screen.
        with readchar.cbreak():
            problem = self.open()
            if problem:
                self.message("Pokemon Trading", problem, RED)
                return
            try:
                self.loop()
            finally:
                self.close()

    def loop(self):
        sys.stdout.write("\033[2J")
        last_draw = 0
        while True:
            if self.pump() or time.monotonic() - last_draw > 0.5:
                self.draw()
                last_draw = time.monotonic()
            key = readchar.readkey_timeout(0.1)
            if key is None:
                continue
            if self.handle_key(key) == "quit":
                return
            self.draw()
            last_draw = time.monotonic()

    def pump(self):
        """React to network events. True if the screen needs redrawing."""
        changed = False
        while not self.notices.empty():
            changed = True
            text, colour = self.notices.get_nowait()
            self.flash(text, colour)
            try:
                self.refresh_items()
            except store.TradeError:
                pass
        while not self.incoming.empty():
            channel, hello, validator = self.incoming.get_nowait()
            if self.session or store.trades_left_today() == 0:
                reason = "busy" if self.session else "no trades left today"
                try:
                    channel.send(proto.message("bye", reason=f"{self.trainer['name'][:20]} is {reason}."))
                except OSError:
                    pass
                channel.close()
                continue
            self.session = proto.TradeSession(channel, self.identity, self.trainer["name"], "b",
                                              first_hello=hello, validator=validator)
            self.session.start()
            self.view = "session"
            sys.stdout.write("\a")
            changed = True
        if self.session:
            while not self.session.events.empty():
                changed = True
                kind, data = self.session.events.get_nowait()
                if kind == "done":
                    self.finish_trade(data)
                    return True
        return changed

    def finish_trade(self, result):
        try:
            animate_send(result["gave"], result["peer"])
            animate_receive(result["got"], result["peer"])
        except Exception:
            pass                 # an animation problem must never hide the result
        self.session = None
        self.view = "list"
        sys.stdout.write("\033[2J")
        self.refresh_items()
        self.flash(f"Trade complete! You got {store.display(result['got'])} from {result['peer']}.")

    # -- keys --------------------------------------------------------------
    def handle_key(self, key):
        if self.view == "list":
            return self.keys_list(key)
        if self.view == "trainers":
            return self.keys_trainers(key)
        if self.view == "address":
            return self.keys_address(key)
        if self.view == "session":
            return self.keys_session(key)

    def move(self, key, cursor, total):
        if key == readchar.key.UP:
            return max(0, cursor - 1)
        if key == readchar.key.DOWN:
            return min(max(0, total - 1), cursor + 1)
        if key == readchar.key.RIGHT:
            return min(max(0, total - 1), (cursor // PAGE_SIZE + 1) * PAGE_SIZE)
        if key == readchar.key.LEFT:
            return max(0, (cursor // PAGE_SIZE - 1) * PAGE_SIZE)
        return cursor

    def keys_list(self, key):
        if key in ("q", "b"):
            return "quit"
        if key == readchar.key.ENTER:
            if not self.items:
                return
            if store.trades_left_today() == 0:
                self.flash(f"You've used all {store.DAILY_LIMIT} trades for today.", RED)
                return
            self.view, self.peer_cursor = "trainers", 0
            self.status = ""
            return
        self.cursor = self.move(key, self.cursor, len(self.items))

    def keys_trainers(self, key):
        peers = self.discovery.nearby()
        if key in ("q", "b", "\x1b"):
            self.view = "list"
            return
        if key == "c":
            self.view, self.address = "address", ""
            return
        if key == readchar.key.ENTER and peers:
            peer = peers[min(self.peer_cursor, len(peers) - 1)]
            self.start_trade(peer["host"], peer["port"], peer["fp"], peer["name"])
            return
        self.peer_cursor = self.move(key, self.peer_cursor, len(peers))

    def keys_address(self, key):
        if key == "\x1b":
            self.view = "trainers"
        elif key in ("\x7f", "\b"):
            self.address = self.address[:-1]
        elif key == readchar.key.ENTER:
            m = re.fullmatch(r"\s*([A-Za-z0-9.\-]+):(\d{1,5})\s*", self.address)
            if not m or not 0 < int(m.group(2)) < 65536:
                self.flash("Type the address shown on the other trainer's screen, e.g. 192.168.1.20:51234", RED)
                return
            self.start_trade(m.group(1), int(m.group(2)), None, m.group(1))
        elif len(key) == 1 and (key.isalnum() or key in ".:-") and len(self.address) < 40:
            self.address += key

    def start_trade(self, host, port, fp, label):
        offer = self.items[self.cursor]
        self.flash(f"Connecting to {label}...", YELLOW)
        self.draw()
        try:
            channel = lan.connect(self.identity, host, port, fp)
        except (OSError, lan.ChannelClosed) as e:
            self.flash(f"Couldn't reach {label}: {e}", RED)
            self.view = "trainers"
            return
        self.session = proto.TradeSession(channel, self.identity, self.trainer["name"], "a",
                                          my_offer={"name": offer["name"], "shiny": offer["shiny"]})
        self.session.start()
        self.view = "session"
        self.status = ""

    def keys_session(self, key):
        snap = self.session.snapshot()
        phase = snap["phase"]
        if phase in ("failed", "pending"):
            self.session = None
            self.view = "list"
            try:
                self.refresh_items()
            except store.TradeError:
                pass
            self.flash(snap["error"] or "", RED if phase == "failed" else YELLOW)
            return
        try:
            if phase == "invited":
                if key == "y":
                    self.session.accept()
                elif key in ("n", "x", "q"):
                    self.session.decline()
                return
            if key in ("x", "q"):
                if not self.session.cancel():
                    self.flash("Almost done - the trade can't be cancelled now.", YELLOW)
                return
            if phase == "choosing" and not snap["my_sig"]:
                if key == readchar.key.ENTER and self.items:
                    item = self.items[self.cursor]
                    self.session.set_offer({"name": item["name"], "shiny": item["shiny"]})
                elif key == "y" and snap["my_offer"] and snap["peer_offer"]:
                    self.session.confirm()
                else:
                    self.cursor = self.move(key, self.cursor, len(self.items))
        except (store.TradeError, OSError) as e:
            self.flash(str(e), RED)

    # -- screens -------------------------------------------------------------
    def header(self, s):
        s.top()
        s.center(f"{YELLOW}Pokemon Trading{RESET}")
        s.sep()
        try:
            left = store.trades_left_today()
        except store.TradeError:
            left = 0
        colour = GREEN if left else RED
        s.row(f"{YELLOW}Trainer:{RESET} {proto.clean_name(self.trainer['name'])}   "
              f"{YELLOW}Level:{RESET} {self.trainer['level']}   "
              f"{YELLOW}Trades left today:{RESET} {colour}{left}/{store.DAILY_LIMIT}{RESET}")
        s.sep()

    def pokemon_rows(self, s, highlight=True):
        if not self.items:
            s.row(f"{RED}You don't have any Pokemon to trade yet.{RESET}")
            s.row(f"{GREEN}Open a few terminals to catch some first!{RESET}")
            for _ in range(PAGE_SIZE - 2):
                s.row()
            return
        start = (self.cursor // PAGE_SIZE) * PAGE_SIZE
        for i in range(start, start + PAGE_SIZE):
            if i >= len(self.items):
                s.row()
                continue
            it = self.items[i]
            text = f"{store.display(it).replace(' (shiny)', ''):<16} {'★ shiny' if it['shiny'] else '':<8}[Owned: {it['count']}]"
            if i == self.cursor and highlight:
                s.row(f"{INVERT}> {text}{RESET}")
            else:
                s.row(f"  {text}")
        pages = max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)
        s.row(f"{GREY}Page {self.cursor // PAGE_SIZE + 1}/{pages}{RESET}")

    def footer(self, s, keys):
        s.sep()
        s.row(keys)
        if self.status:
            s.sep()
            s.wrap(self.status, self.status_colour)
        s.bottom()

    def draw(self):
        s = Screen()
        self.header(s)
        if self.view == "list":
            self.draw_list(s)
        elif self.view == "trainers":
            self.draw_trainers(s)
        elif self.view == "address":
            self.draw_address(s)
        elif self.view == "session" and self.session:
            self.draw_session(s)
        s.show()

    def pending_line(self, s):
        try:
            pending = store.pending_trades()
        except store.TradeError:
            return
        for p in pending[:2]:
            s.wrap(f"Waiting to finish a trade with {proto.clean_name(p['peer']['name'])} "
                   f"({store.display(p['give'])} for {store.display(p['receive'])}) - it completes "
                   f"automatically when you're both on this page.", YELLOW)

    def draw_list(self, s):
        s.row(f"{YELLOW}Your Pokemon:{RESET}")
        self.pokemon_rows(s)
        if self.items:
            s.sep()
            s.row(f"{YELLOW}Selected:{RESET} {pokemon_label(self.items[self.cursor])}")
        self.pending_line(s)
        self.footer(s, f"{GREEN}Enter{RESET} = Offer trade  {GREEN}↑/↓ ←/→{RESET} = Move  {YELLOW}q{RESET} = Back")

    def draw_trainers(self, s):
        offer = self.items[self.cursor]
        s.row(f"{YELLOW}Offering:{RESET} {pokemon_label(offer)}")
        s.sep()
        s.row(f"{YELLOW}Trainers nearby with their trade page open:{RESET}")
        peers = self.discovery.nearby()
        self.peer_cursor = max(0, min(self.peer_cursor, len(peers) - 1))
        if not peers:
            self.discovery_help(s)
        for i, p in enumerate(peers[:PAGE_SIZE]):
            known = store.known_trainer(p["fp"])
            tag = f"{GREEN}(traded before){RESET}" if known else (
                f"{RED}(not the {p['name']} you traded with before!){RESET}"
                if store.name_conflict(p["name"], p["fp"]) else "")
            line = f"{p['name']:<16} {tag}"
            s.row(f"{INVERT}> {line}{RESET}" if i == self.peer_cursor else f"  {line}")
        for _ in range(max(0, 4 - max(len(peers), 2))):
            s.row()
        s.sep()
        s.row(f"{GREY}Not listed? Press c to type an address.{RESET}")
        s.row(f"{GREY}Your address: {self.address_text}{RESET}")
        self.footer(s, f"{GREEN}Enter{RESET} = Invite  {GREEN}c{RESET} = Enter address  {YELLOW}b{RESET} = Back")

    def discovery_help(self, s):
        """Why nobody's listed yet, and what to do about it."""
        d = self.discovery
        if d.blocked():
            if sys.platform == "darwin":
                app = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Visual Studio Code",
                       "WarpTerminal": "Warp", "ghostty": "Ghostty"}.get(os.environ.get("TERM_PROGRAM", ""),
                                                                         "your terminal app")
                s.wrap(f"macOS is blocking {app} from your local network, so nobody can see you. "
                       f"Turn it on in System Settings > Privacy & Security > Local Network, "
                       f"then open this page again.", RED)
            else:
                s.wrap("Your system is blocking poketerm from sending on the local network "
                       "(a firewall?), so nobody can see you.", RED)
            return
        nets = ", ".join(f"{n} {a}" for n, a, _ in d.interfaces[:2]) or "no network found"
        s.row(f"{GREY}Looking for trainers on your network ({nets})...{RESET}")
        s.row(f"{GREY}Ask them to open their Pokedex and press t.{RESET}")
        if time.monotonic() - d.started > 10:
            s.wrap("Still nobody? Check you're on the same Wi-Fi. Guest and office networks often hide "
                   "devices from each other; press c to type their address instead. On a Mac, both of "
                   "you may need to allow your terminal app in System Settings > Privacy & Security > "
                   "Local Network.", YELLOW)

    def draw_address(self, s):
        s.row(f"{YELLOW}Connect by address{RESET}")
        s.row("Type the address shown on the other trainer's trade page:")
        s.row(f"> {self.address}{INVERT} {RESET}")
        s.row(f"{GREY}Your address is {self.address_text}{RESET}")
        self.footer(s, f"{GREEN}Enter{RESET} = Connect  {YELLOW}Esc{RESET} = Back")

    def draw_session(self, s):
        snap = self.session.snapshot()
        phase, peer = snap["phase"], snap["peer_name"]
        code = snap["code"] or "--- ---"

        if phase == "handshake":
            s.row("Connecting securely...")
            self.footer(s, f"{YELLOW}x{RESET} = Cancel")
            return
        if phase in ("failed", "pending"):
            colour = RED if phase == "failed" else YELLOW
            s.wrap(snap["error"] or "The trade stopped.", colour)
            self.footer(s, "Press any key to continue")
            return
        if phase == "invited":
            s.row(f"{BOLD}{peer}{RESET} wants to trade with you!")
            if snap["peer_offer"]:
                s.row(f"They're offering: {pokemon_label(snap['peer_offer'])}")
            if snap["peer_trades_left"] is not None:
                s.row(f"{GREY}{peer} has {snap['peer_trades_left']} trades left today.{RESET}")
            s.sep()
            s.row(f"{YELLOW}Pairing code:{RESET} {BOLD}{code}{RESET}")
            s.wrap(f"Check that {peer}'s screen shows the same code. If it doesn't, press n - "
                   f"someone else may be trying to join in.", GREY)
            self.footer(s, f"{GREEN}y{RESET} = Accept   {RED}n{RESET} = Decline")
            return
        if phase == "waiting":
            s.row(f"Waiting for {BOLD}{peer}{RESET} to accept...")
            s.sep()
            s.row(f"{YELLOW}Pairing code:{RESET} {BOLD}{code}{RESET}")
            s.wrap(f"{peer}'s screen should show the same code. If it doesn't, cancel.", GREY)
            self.footer(s, f"{YELLOW}x{RESET} = Cancel")
            return

        # choosing / committing
        give = pokemon_label(snap["my_offer"]) if snap["my_offer"] else f"{GREY}choose below and press Enter{RESET}"
        get = pokemon_label(snap["peer_offer"]) if snap["peer_offer"] else f"{GREY}waiting for {peer} to choose...{RESET}"
        s.row(f"{YELLOW}Trading with:{RESET} {peer}    {YELLOW}Code:{RESET} {code}")
        s.sep()
        s.row(f"{RED}You give:{RESET} {give}")
        s.row(f"{GREEN}You get:{RESET}  {get}")
        mine = f"{GREEN}✔ you confirmed{RESET}" if snap["my_sig"] else f"{GREY}you haven't confirmed{RESET}"
        theirs = f"{GREEN}✔ {peer} confirmed{RESET}" if snap["peer_sig"] else f"{GREY}{peer} hasn't confirmed{RESET}"
        s.row(f"{mine}   {theirs}")
        s.sep()
        if phase == "committing":
            s.row(f"{YELLOW}Trading... don't close the terminal.{RESET}")
            self.footer(s, f"{GREY}Please wait{RESET}")
            return
        if snap["my_sig"]:
            s.row(f"Waiting for {peer} to confirm...")
            self.footer(s, f"{YELLOW}x{RESET} = Cancel trade")
            return
        s.row(f"{YELLOW}Your Pokemon:{RESET}")
        self.pokemon_rows(s)
        keys = f"{GREEN}Enter{RESET} = Offer this one  "
        if snap["my_offer"] and snap["peer_offer"]:
            keys += f"{GREEN}y{RESET} = Confirm trade  "
        self.footer(s, keys + f"{YELLOW}x{RESET} = Cancel")

    def message(self, title, text, colour):
        s = Screen()
        s.top(); s.center(f"{YELLOW}{title}{RESET}"); s.sep()
        s.wrap(text, colour)
        s.sep(); s.row("Press any key to go back"); s.bottom()
        sys.stdout.write("\033[2J")
        s.show()
        readchar.readkey()


def open_trade_page():
    page = TradePage()
    try:
        page.run()
    except KeyboardInterrupt:
        page.close()
