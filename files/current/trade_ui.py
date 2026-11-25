"""The trading screens in the Pokedex (press `t`), and the trade animations."""
import errno
import json
import os
import queue
import re
import shutil
import socket
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

WIDTH = 50                  # the trade screen's width on the device
STATUS_SECONDS = 15         # how long a message under the screen stays
PAGE_SIZE = 10
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GO_COLORSCRIPTS = Path.home() / ".local/share/pokemon-go-colorscripts/pokemon-go-colorscripts"
TRAINER_FILE = Path.home() / ".local/share/poketerm/trainer.json"
DEBUG_LOG = Path.home() / ".local/share/poketerm/trade/debug.log"
_DEBUG = os.environ.get("POKETERM_TRADE_DEBUG") == "1"
_T0 = time.monotonic()


def debug(event):
    """With POKETERM_TRADE_DEBUG=1, log timings to trade/debug.log (for bug reports)."""
    if _DEBUG:
        try:
            with open(DEBUG_LOG, "a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} +{time.monotonic() - _T0:7.3f}s  {event}\n")
        except OSError:
            pass


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
    """Collects one trade screen's lines; show() draws them on the device."""

    def __init__(self, page=None):
        self.page = page
        self.lines = []
        self.keys = []

    def top(self):
        pass

    def bottom(self):
        pass

    def sep(self):
        self.lines.append(f"{GREY}{'─' * WIDTH}{RESET}")

    def row(self, text=""):
        self.lines.append(fit(text, WIDTH))

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
        import device
        page = self.page
        info, slots, lights, status = ([], ("Trade", ""), (True, False, False), "Trading")
        if page is not None:
            info, slots, lights, status = page.side()
        device.show(self.lines, width=WIDTH, info=info, keys=self.keys, slots=slots,
                    lights=lights, status=status, title="Pokemon Trading")


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
        return []                     # callers show "(no sprite)" instead
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


class Animation:
    """Frames of a trade animation, played on the device's screen. Any key skips."""

    def __init__(self, pokemon, peer_name, heading, size=None):
        import device
        self.device = device
        self.sprite = get_sprite(pokemon) or ["", f"{GREY}(no sprite){RESET}"]
        self.sw = max(visible(l) for l in self.sprite)
        self.sh = len(self.sprite)
        # Both animations of a trade share one size, so the device never
        # changes shape between sending and receiving.
        self.W, self.H = size or animation_size([pokemon])
        self.left = (self.W - self.sw) // 2
        self.top = 2
        self.ball_y = self.top + max(0, (self.sh - BALL_H) // 2)
        self.heading = heading
        self.pokemon = pokemon
        self.peer = peer_name
        self.skip = False

    def frame(self, sprite_rows=None, ball_x=None, lift=0, sparkle=False, caption=""):
        rows = [""] * self.H
        rows[0] = f"{YELLOW}{BOLD}{self.heading:^{self.W}}{RESET}"
        if sprite_rows is not None:
            first, count = sprite_rows
            for i in range(first, min(first + count, self.sh)):
                rows[self.top + i] = " " * self.left + self.sprite[i]
        if ball_x is not None:
            x = max(0, ball_x)
            for i, (colour, line) in enumerate(BALL):
                y = self.ball_y + i - (lift if i < 2 else 0)
                if 0 < y < self.H - 1:
                    rows[y] = fit(" " * x + colour + line, self.W)      # slides off the edge
            if sparkle:
                rows[max(1, self.ball_y - lift - 1)] = fit(" " * (x + BALL_W // 2) + WHITE + BOLD + "✦", self.W)
        if caption:
            rows[self.H - 1] = " " * max(0, (self.W - visible(caption)) // 2) + caption
        self.device.show(rows, width=self.W, wrap=False,
                         info=[f"{BOLD}TRADING{RESET}", "", f"With {self.peer}", "",
                               store.display(self.pokemon)],
                         keys=[("any", "Skip")], slots=("Trading", self.peer[:12]),
                         lights=(True, True, True), status="Trading...", title="Trade")

    def pause(self, seconds):
        if not self.skip and readchar.readkey_timeout(seconds) is not None:
            self.skip = True


def animation_size(pokemons):
    """(width, height) of a screen that fits every one of these Pokemon."""
    sprites = [get_sprite(p) or [""] for p in pokemons]
    w = max(visible(l) for sp in sprites for l in sp) if sprites else 0
    h = max(len(sp) for sp in sprites) if sprites else 0
    return max(WIDTH, w + 4), max(h + 5, BALL_H + 7)


def animate_send(pokemon, peer_name, size=None):
    """Your Pokemon goes back into its Poke Ball and flies off to the other trainer."""
    a = Animation(pokemon, peer_name, "SENDING", size)
    name = store.display(pokemon).replace(" (shiny)", "")
    a.frame((0, a.sh), caption=f"{YELLOW}Take care, {name}!{RESET}")
    a.pause(1.0)
    step = max(1, a.sh // 8)
    for shown in range(a.sh, 0, -step):              # it shrinks back into the ball
        a.frame(((a.sh - shown) // 2, shown), caption=f"{YELLOW}{name}, return!{RESET}")
        a.pause(0.04)
    centre = (a.W - BALL_W) // 2
    a.frame(ball_x=centre, caption=f"{YELLOW}{name}, return!{RESET}")
    a.pause(0.4)
    for x in range(centre, a.W, 3):                  # ...and hops away
        a.frame(ball_x=x, lift=0, caption="")
        a.pause(0.03)
    a.frame(caption=f"{GREEN}{name} was sent to {peer_name}.{RESET}")
    a.pause(1.0)


def animate_receive(pokemon, peer_name, size=None):
    """A Poke Ball arrives, wobbles, opens, and your new Pokemon appears."""
    a = Animation(pokemon, peer_name, "RECEIVING", size)
    name = store.display(pokemon).replace(" (shiny)", "")
    centre = (a.W - BALL_W) // 2
    arriving = f"{YELLOW}{peer_name} sent you a Poke Ball!{RESET}"
    for x in range(0, centre, 3):
        a.frame(ball_x=x, caption=arriving)
        a.pause(0.03)
    a.frame(ball_x=centre, caption=arriving)
    a.pause(0.4)
    for shift in (-1, 1, -1, 1, 0):                  # wobble
        a.frame(ball_x=centre + shift, caption=arriving)
        a.pause(0.12 if shift else 0.35)
    a.frame(ball_x=centre, lift=2, sparkle=True, caption=arriving)      # it opens
    a.pause(0.3)
    step = max(1, a.sh // 10)
    for shown in range(0, a.sh + 1, step):           # the Pokemon appears
        a.frame((0, shown), caption=arriving)
        a.pause(0.03)
    shiny = f"  {YELLOW}★ It's shiny!{RESET}" if pokemon.get("shiny") else ""
    a.frame((0, a.sh), caption=f"{GREEN}{peer_name} sent you {name}!{RESET}{shiny}")
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
        self.view = "trainers"               # trainers | address | connecting | session
        self.connecting = None               # {"label", "cancelled"} while connecting
        self.picking = None                  # {"item", "recs", "cursor"}: choosing which one to send
        self.connected = queue.Queue()       # (attempt, channel or error) from the connect thread
        self.peer_cursor = 0
        self.address = ""
        self.status = ""
        self.status_colour = GREEN
        self.status_at = 0.0
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
        """Leave the trade page without waiting on the network.

        Everything here talks to other computers (telling a trainer we left,
        closing connections), so it runs in the background and you're back in
        the Pokedex at once. That's safe: a trade that hasn't started yet has
        nothing in escrow, and one that has started can't be cancelled anyway."""
        debug("close: start")
        self.stop.set()
        if self.connecting:
            self.connecting["cancelled"] = True
        session, parts, incoming = self.session, (self.discovery, self.listener), self.incoming

        def teardown():
            if session:
                try:
                    session.cancel()
                except Exception:
                    pass
                debug("close: session cancelled")
            for part in parts:
                if part:
                    part.close()
            while not incoming.empty():
                try:
                    channel, _, _ = incoming.get_nowait()
                    channel.close(linger=0)
                except (queue.Empty, OSError):
                    pass
            debug("close: network closed")
        threading.Thread(target=teardown, daemon=True).start()
        debug("close: returned")

    def refresh_items(self):
        self.items = store.tradeable_pokemon()
        self.cursor = max(0, min(self.cursor, len(self.items) - 1))
        self._journal_at = 0            # trades may have changed: re-read on next draw

    def journal_info(self):
        """(trades left today, pending trades), re-read at most every 2 seconds:
        each read briefly takes the Pokedex lock, and the screen redraws often."""
        if time.monotonic() - getattr(self, "_journal_at", 0) > 2:
            try:
                self._journal = (store.trades_left_today(), store.pending_trades())
            except store.TradeError:
                self._journal = (0, [])
            self._journal_at = time.monotonic()
        return self._journal

    def flash(self, text, colour=GREEN):
        """Show a message under the current screen. It clears after
        STATUS_SECONDS, when a trade connects, or when the screen changes."""
        self.status, self.status_colour, self.status_at = text, colour, time.monotonic()

    def clear_status(self):
        self.status = ""

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
        debug("trade page: opening")
        with readchar.cbreak():
            problem = self.open()
            debug("trade page: open" + (f" (problem: {problem})" if problem else ""))
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
                debug(f"key {key!r}: quit")
                return
            self.draw()
            last_draw = time.monotonic()

    def pump(self):
        """React to network events. True if the screen needs redrawing."""
        changed = self.finish_connect()
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
            try:
                self.refresh_items()
            except store.TradeError:
                self.items = []
            name = self.trainer["name"][:20]
            if self.session:
                busy = f"{name} is busy with another trade."
            elif store.trades_left_today() == 0:
                busy = f"{name} has no trades left today."
            elif not self.items:
                busy = f"{name} has no Pokemon to trade yet."
            else:
                busy = None
            if busy:
                try:
                    channel.send(proto.message("bye", reason=busy))
                except OSError:
                    pass
                channel.close()
                continue
            self.session = proto.TradeSession(channel, self.identity, self.trainer["name"], "b",
                                              first_hello=hello, validator=validator)
            self.cursor = 0
            self.clear_status()
            self.session.start()
            self.view = "session"
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
            import settings
            if settings.animations_on():                  # can be turned off in settings
                size = animation_size([result["gave"], result["got"]])
                animate_send(result["gave"], result["peer"], size)
                animate_receive(result["got"], result["peer"], size)
        except Exception:
            pass                 # an animation problem must never hide the result
        self.session = None
        self.picking = None
        self.view = "trainers"
        sys.stdout.write("\033[2J")
        self.refresh_items()
        message = f"Trade complete! You got {store.display(result['got'])} from {result['peer']}."
        unlocked = self.new_achievements()
        if unlocked:
            message += " Achievement unlocked: " + ", ".join(unlocked) + "!"
        self.flash(message)

    def new_achievements(self):
        """Check achievements now (trades can earn some) and return any new ones."""
        try:
            import achievements
            before = {a["name"] for a in achievements.load_trainer().get("hall_of_fame", [])}
            achievements.check_pokedex_achievements()
            after = achievements.load_trainer().get("hall_of_fame", [])
            return [a["name"] for a in after if a["name"] not in before]
        except Exception:
            return []           # achievements must never get in the way of a trade

    # -- keys --------------------------------------------------------------
    def handle_key(self, key):
        if self.view == "trainers":
            return self.keys_trainers(key)
        if self.view == "address":
            return self.keys_address(key)
        if self.view == "connecting":
            if key in ("q", "x", "b", "\x1b"):
                if self.connecting:
                    self.connecting["cancelled"] = True
                self.connecting, self.view = None, "trainers"
            return
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

    def can_trade(self):
        """True if a trade can start now; otherwise says why."""
        if not self.items:
            self.flash("You don't have any Pokemon to trade yet - catch some first!", RED)
            return False
        if store.trades_left_today() == 0:
            self.flash(f"You've used all {store.DAILY_LIMIT} trades for today.", RED)
            return False
        return True

    def keys_trainers(self, key):
        peers = self.discovery.nearby()
        if key in ("q", "b", "\x1b"):
            return "quit"
        if key == "c":
            self.view, self.address = "address", ""
            self.clear_status()
            return
        if key == readchar.key.ENTER and peers and self.can_trade():
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
            if self.can_trade():
                self.start_trade(m.group(1), int(m.group(2)), None, m.group(1))
        elif len(key) == 1 and (key.isalnum() or key in ".:-") and len(self.address) < 40:
            self.address += key

    def start_trade(self, host, port, fp, label):
        """Invite a trainer. Both of you choose your Pokemon once they accept.

        Connecting happens in the background, so the screen stays responsive
        (and q/x leave at once) even if the other computer doesn't answer."""
        attempt = {"label": label, "cancelled": False}
        self.connecting, self.view, self.status = attempt, "connecting", ""

        def work():
            # macOS can refuse the first local-network connection right after
            # an app is given access ("No route to host"): try a couple more times.
            for tries in range(3):
                try:
                    result = lan.connect(self.identity, host, port, fp, timeout=6)
                    break
                except (OSError, lan.ChannelClosed) as e:
                    result = e
                    if getattr(e, "errno", None) != errno.EHOSTUNREACH or attempt["cancelled"]:
                        break
                    time.sleep(1)
            if not isinstance(result, lan.Channel):
                result.address = f"{host}:{port}"
            self.connected.put((attempt, result))
        threading.Thread(target=work, daemon=True).start()

    def finish_connect(self):
        """Pick up a finished connection attempt. True if the screen changed."""
        changed = False
        while not self.connected.empty():
            attempt, result = self.connected.get_nowait()
            if attempt is not self.connecting or attempt["cancelled"]:
                if isinstance(result, lan.Channel):
                    result.close(linger=0)          # given up on: hang up
                continue
            self.connecting = None
            changed = True
            if not isinstance(result, lan.Channel):
                self.view = "trainers"
                self.flash(self.connect_problem(attempt["label"], result), RED)
                continue
            self.session = proto.TradeSession(result, self.identity, self.trainer["name"], "a")
            self.cursor = 0
            self.clear_status()
            self.session.start()
            self.view = "session"
        return changed

    def search_items(self):
        """Search the Pokemon you can trade; the list's cursor moves to the one chosen."""
        import search
        order = store.dex_order()
        entries = [{"name": it["name"], "no": order.get(it["name"], 0), "shiny": it["shiny"],
                    "count": it["count"], "index": i} for i, it in enumerate(self.items)]

        def describe(e):
            star = f"{YELLOW}★{RESET}" if e["shiny"] else " "
            return f"{e['no']:03d}  {store.display(e).replace(' (shiny)', '')[:16]:<16} {star} {GREY}x{e['count']}{RESET}"

        found = search.search(entries, "FIND A POKEMON TO TRADE", describe, width=WIDTH,
                              info=[f"{BOLD}SEARCH{RESET}", "", "Your Pokemon you", "can trade.", "",
                                    "★ = shiny"], slots=("Trade", f"{len(entries)} kinds"), status="Search")
        if found:
            self.cursor = found["index"]
        sys.stdout.write("\033[2J")

    def offer_lines(self, snap):
        """One line about each Pokemon on offer (nature, when caught, by whom)."""
        try:
            import collection
        except Exception:
            return "", ""
        mine = collection.summary(snap.get("my_details")) if snap.get("my_details") else ""
        theirs = ""
        if snap.get("peer_offer"):
            clean = collection.clean_details(snap.get("peer_details"), snap["peer_offer"]["name"],
                                             snap["peer_offer"]["shiny"])
            theirs = collection.summary(clean) if clean else ""
        return mine, theirs

    def individuals(self, item):
        """Your Pokemon of this kind, each with its own story (oldest first)."""
        try:
            import collection
            with store.pokedex_lock():
                return collection.of_kind(item["name"], item["shiny"])
        except Exception:
            return []

    def offer(self, item, rec):
        import collection
        details = collection.for_trade(rec) if rec else None
        self.session.set_offer({"name": item["name"], "shiny": item["shiny"]}, details)

    def terminal_app(self):
        return {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Visual Studio Code",
                "WarpTerminal": "Warp", "ghostty": "Ghostty"}.get(os.environ.get("TERM_PROGRAM", ""),
                                                                  "your terminal app")

    def connect_problem(self, label, e):
        """A plain-words reason a connection failed, and what to do about it."""
        code = getattr(e, "errno", None)
        where = getattr(e, "address", "")
        if code in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EPERM, errno.EACCES) and sys.platform == "darwin":
            return (f"macOS blocked the connection to {label} ({where}). Check {self.terminal_app()} is allowed in "
                    f"System Settings > Privacy & Security > Local Network on this Mac. If it already is, quit "
                    f"and reopen {self.terminal_app()} - macOS only applies the setting to newly opened apps.")
        if code in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            return f"Couldn't reach {label}: no route to their computer. Are you on the same network?"
        if code == errno.ECONNREFUSED:
            return f"{label} isn't on their trade page any more."
        if isinstance(e, (TimeoutError, socket.timeout)) or code == errno.ETIMEDOUT:
            return (f"{label} didn't answer. A firewall on their computer may be blocking poketerm "
                    f"(on a Mac: allow Python when macOS asks about incoming connections).")
        return f"Couldn't reach {label}: {e}"

    def keys_session(self, key):
        snap = self.session.snapshot()
        phase = snap["phase"]
        if phase in ("failed", "pending"):
            self.session = None
            self.picking = None
            self.view = "trainers"
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
            if phase == "choosing" and not snap["my_sig"] and self.picking:
                pick = self.picking
                if key == readchar.key.ENTER:
                    rec = pick["recs"][pick["cursor"]]
                    if rec.get("favourite"):
                        self.flash("That one's a ♥ favourite, so it can't be traded. "
                                   "Unmark it in the Pokedex first.", YELLOW)
                        return
                    self.picking = None
                    self.offer(pick["item"], rec)
                elif key in ("b", "\x1b"):
                    self.picking = None
                else:
                    pick["cursor"] = self.move(key, pick["cursor"], len(pick["recs"]))
                return
            if phase == "choosing" and not snap["my_sig"]:
                if key == "/" and self.items:
                    self.search_items()
                    return
                if key == readchar.key.ENTER and self.items:
                    item = self.items[self.cursor]
                    recs = self.individuals(item)
                    tradeable = [i for i, r in enumerate(recs) if not r.get("favourite")]
                    if recs and not tradeable:
                        what = "it's a ♥ favourite" if len(recs) == 1 else "they're all ♥ favourites"
                        self.flash(f"You can't trade that {pokemon_label(item)} - {what}. "
                                   f"Unmark it in the Pokedex first.", YELLOW)
                    elif len(recs) > 1:
                        self.picking = {"item": item, "recs": recs, "cursor": tradeable[0]}   # which one?
                    else:
                        self.offer(item, recs[0] if recs else None)
                elif key == "y" and snap["my_offer"] and snap["peer_offer"]:
                    self.session.confirm()
                else:
                    self.cursor = self.move(key, self.cursor, len(self.items))
        except (store.TradeError, OSError) as e:
            self.flash(str(e), RED)

    # -- screens -------------------------------------------------------------
    def side(self):
        """What the device's right half shows: (info lines, slots, lights, status)."""
        left = self.journal_info()[0]
        if self.session:
            snap = self.session.snapshot()
            info = [f"{BOLD}TRADING WITH{RESET}", f"{snap['peer_name']}", "",
                    f"{BOLD}PAIRING CODE{RESET}", f"{snap['code'] or '--- ---'}", ""]
            if snap["my_offer"] or snap["peer_offer"]:
                mine_line, theirs_line = self.offer_lines(snap)
                info += ["You give:", "  " + (store.display(snap["my_offer"]) if snap["my_offer"] else "...")]
                info += ["  " + mine_line.split(" · ")[0]] if mine_line else []
                info += ["You get:", "  " + (store.display(snap["peer_offer"]) if snap["peer_offer"] else "...")]
                info += ["  " + theirs_line.split(" · ")[0]] if theirs_line else []
            return info, ("Trading", snap["peer_name"][:12]), (True, True, bool(snap["peer_sig"])), "Trading..."
        nearby = len(self.discovery.nearby()) if self.discovery else 0
        info = [f"{BOLD}POKEMON TRADING{RESET}", "", proto.clean_name(self.trainer["name"]),
                f"Level {self.trainer['level']}", "", f"Trades left: {left}/{store.DAILY_LIMIT}",
                f"Trainers nearby: {nearby}", f"Your Pokemon: {sum(i['count'] for i in self.items)}"]
        return info, ("Trade", f"{left}/{store.DAILY_LIMIT} left"), (True, nearby > 0, left > 0), "Trade"

    def header(self, s):
        s.center(f"{YELLOW}{BOLD}POKEMON TRADING{RESET}")
        s.sep()
        left = self.journal_info()[0]
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
        """keys: [(key, label)] for the device's buttons; then any message."""
        s.keys = list(keys)
        if self.status and time.monotonic() - self.status_at > STATUS_SECONDS:
            self.status = ""
        if self.status:
            s.sep()
            s.wrap(self.status, self.status_colour)
        s.bottom()

    def draw(self):
        started = time.monotonic()
        try:
            self._draw()
        finally:
            took = time.monotonic() - started
            if took > 0.2:
                debug(f"slow draw ({self.view}): {took:.2f}s")

    def _draw(self):
        s = Screen(self)
        self.header(s)
        if self.view == "trainers":
            self.draw_trainers(s)
        elif self.view == "address":
            self.draw_address(s)
        elif self.view == "connecting" and self.connecting:
            s.row(f"Connecting to {BOLD}{self.connecting['label']}{RESET}...")
            self.footer(s, [("x", "Cancel")])
        elif self.view == "session" and self.session:
            self.draw_session(s)
        s.show()

    def pending_line(self, s):
        for p in self.journal_info()[1][:2]:
            s.wrap(f"Waiting to finish a trade with {proto.clean_name(p['peer']['name'])} "
                   f"({store.display(p['give'])} for {store.display(p['receive'])}) - it completes "
                   f"automatically when you're both on this page.", YELLOW)

    def draw_trainers(self, s):
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
        self.pending_line(s)
        if not self.items:
            s.wrap("You don't have any Pokemon to trade yet - catch some first!", RED)
        self.footer(s, [("⏎", "Invite"), ("↑↓", "Move"), ("c", "Address"), ("q", "Back")])

    def discovery_help(self, s):
        """Why nobody's listed yet, and what to do about it."""
        d = self.discovery
        if d.blocked():
            if sys.platform == "darwin":
                app = self.terminal_app()
                s.wrap(f"macOS is blocking {app} from your local network, so nobody can see you. "
                       f"Turn it on in System Settings > Privacy & Security > Local Network, "
                       f"then open this page again.", RED)
            else:
                s.wrap("Your system is blocking poketerm from sending on the local network "
                       "(a firewall?), so nobody can see you.", RED)
            return
        nets = ", ".join(f"{n} {a}" for n, a, _ in d.interfaces[:2]) or "no network found"
        s.wrap(f"Looking for trainers on your network ({nets})...", GREY)
        s.row(f"{GREY}Ask them to open their Pokedex and press t.{RESET}")
        if time.monotonic() - d.started > 10:
            s.wrap("Still nobody? Check you're on the same Wi-Fi. Guest and office networks often hide "
                   "devices from each other; press c to type their address instead. On a Mac, both of "
                   "you may need to allow your terminal app in System Settings > Privacy & Security > "
                   "Local Network.", YELLOW)

    def draw_address(self, s):
        s.row(f"{YELLOW}Connect by address{RESET}")
        s.wrap("Type the address shown on the other trainer's trade page:")
        s.row(f"> {self.address}{INVERT} {RESET}")
        s.row(f"{GREY}Your address is {self.address_text}{RESET}")
        self.footer(s, [("⏎", "Connect"), ("Esc", "Back")])

    def draw_session(self, s):
        snap = self.session.snapshot()
        phase, peer = snap["phase"], snap["peer_name"]
        code = snap["code"] or "--- ---"

        if phase == "handshake":
            s.row("Connecting securely...")
            self.footer(s, [("x", "Cancel")])
            return
        if phase in ("failed", "pending"):
            colour = RED if phase == "failed" else YELLOW
            s.wrap(snap["error"] or "The trade stopped.", colour)
            self.footer(s, [("any", "Continue")])
            return
        if phase == "invited":
            s.row(f"{BOLD}{peer}{RESET} wants to trade with you!")
            if snap["peer_offer"]:
                s.row(f"They're offering: {pokemon_label(snap['peer_offer'])}")
            else:
                s.wrap("If you accept, you'll both choose a Pokemon to trade.", GREY)
            if snap["peer_trades_left"] is not None:
                s.row(f"{GREY}{peer} has {snap['peer_trades_left']} trades left today.{RESET}")
            s.sep()
            s.row(f"{YELLOW}Pairing code:{RESET} {BOLD}{code}{RESET}")
            s.wrap(f"Check that {peer}'s screen shows the same code. If it doesn't, press n - "
                   f"someone else may be trying to join in.", GREY)
            self.footer(s, [("y", "Accept"), ("n", "Decline")])
            return
        if phase == "waiting":
            s.row(f"Waiting for {BOLD}{peer}{RESET} to accept...")
            s.sep()
            s.row(f"{YELLOW}Pairing code:{RESET} {BOLD}{code}{RESET}")
            s.wrap(f"{peer}'s screen should show the same code. If it doesn't, cancel.", GREY)
            self.footer(s, [("x", "Cancel")])
            return

        # choosing / committing
        give = pokemon_label(snap["my_offer"]) if snap["my_offer"] else f"{GREY}choose below and press Enter{RESET}"
        get = pokemon_label(snap["peer_offer"]) if snap["peer_offer"] else f"{GREY}waiting for {peer} to choose...{RESET}"
        s.row(f"{YELLOW}Trading with:{RESET} {peer}    {YELLOW}Code:{RESET} {code}")
        s.sep()
        s.row(f"{RED}You give:{RESET} {give}")
        mine_line, theirs_line = self.offer_lines(snap)
        if mine_line:
            s.row(f"          {GREY}{mine_line}{RESET}")
        s.row(f"{GREEN}You get:{RESET}  {get}")
        if theirs_line:
            s.row(f"          {GREY}{theirs_line}{RESET}")
        mine = f"{GREEN}✔ you confirmed{RESET}" if snap["my_sig"] else f"{GREY}you haven't confirmed{RESET}"
        theirs = f"{GREEN}✔ {peer} confirmed{RESET}" if snap["peer_sig"] else f"{GREY}{peer} hasn't confirmed{RESET}"
        s.row(f"{mine}   {theirs}")
        s.sep()
        if phase == "committing":
            s.row(f"{YELLOW}Trading... don't close the terminal.{RESET}")
            self.footer(s, [])
            return
        if snap["my_sig"]:
            s.row(f"Waiting for {peer} to confirm...")
            self.footer(s, [("x", "Cancel")])
            return
        if self.picking:
            pick = self.picking
            s.row(f"{YELLOW}Which {pokemon_label(pick['item'])} will you send?{RESET}")
            import collection
            for i, rec in enumerate(pick["recs"][:PAGE_SIZE]):
                no = f"#{rec['catch_no']} " if rec.get("catch_no") else ""
                text = f"{no}{collection.summary(rec)}"
                if rec.get("favourite"):
                    text = f"{GREY}{text} (can't trade){RESET}"
                s.row(f"{INVERT}> {fit(text, WIDTH - 2)}{RESET}" if i == pick["cursor"] else f"  {fit(text, WIDTH - 2)}")
            self.footer(s, [("⏎", "Send this"), ("↑↓", "Move"), ("b", "Back")])
            return
        s.row(f"{YELLOW}Your Pokemon:{RESET}")
        self.pokemon_rows(s)
        keys = [("⏎", "Offer"), ("↑↓", "Move"), ("/", "Search")]
        if snap["my_offer"] and snap["peer_offer"]:
            keys.append(("y", "Confirm"))
        self.footer(s, keys + [("x", "Cancel")])

    def message(self, title, text, colour):
        s = Screen(self)
        s.center(f"{YELLOW}{title}{RESET}"); s.sep()
        s.wrap(text, colour)
        s.keys = [("any", "Back")]
        s.show()
        readchar.readkey()


def open_trade_page():
    page = TradePage()
    try:
        page.run()
    except KeyboardInterrupt:
        page.close()
    debug("trade page: back to the Pokedex")
