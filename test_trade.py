#!/usr/bin/env python3
"""Trading tests, run by test.sh: real trades between separate processes
(each its own HOME, like two users) over the real network code, including
trainers that crash at each critical moment.

  python3 test_trade.py SANDBOX_DIR      run all scenarios, print PASS/FAIL lines
  python3 test_trade.py party ...        (internal) one side of a trade
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

REPO = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# One side of a trade (runs in its own process with its own HOME)
# ---------------------------------------------------------------------------
def party(argv):
    mode, give, rendezvous = argv[0], argv[1], argv[2]
    faults = set((argv[3] if len(argv) > 3 else "none").split(","))
    app = os.path.expanduser("~/.local/share/poketerm")
    sys.path.insert(0, app)
    import trade_lan as lan
    import trade_protocol as proto
    import trade_store as store

    ident = store.load_identity()
    name, _, variant = give.partition(":")
    offer = {"name": name, "shiny": variant == "shiny"}
    me = "Ash" if mode.startswith("connect") else "Misty"
    result = {}

    # Faults are injected from outside the production code.
    real_prepare = store.prepare
    if "die_before_ready" in faults:
        def prepare(*a, **k):
            real_prepare(*a, **k)
            os._exit(9)
        store.prepare = prepare

    def wrap(ch):
        real_send = ch.send

        def send(obj):
            if "die_before_confirm" in faults and obj.get("type") == "confirm":
                os._exit(9)
            real_send(obj)
            if "die_after_confirm" in faults and obj.get("type") == "confirm":
                os._exit(9)
            if "die_after_ready" in faults and obj.get("type") == "ready":
                os._exit(9)
        ch.send = send
        return ch

    def run(sess):
        sess.start()
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                kind, data = sess.events.get(timeout=1)
            except Exception:
                continue
            snap = sess.snapshot()
            if kind == "handshake":
                result["code"] = snap["code"]
                if sess.role == "b":
                    if "decline" in faults:
                        sess.decline()
                    else:
                        sess.accept()
                        sess.set_offer(offer)
            elif kind == "changed":
                if snap["phase"] == "choosing" and snap["my_offer"] and snap["peer_offer"] and not snap["my_sig"]:
                    if "slow_confirm" in faults:
                        time.sleep(2)
                    sess.confirm()
                    if "cancel_after_confirm" in faults:
                        result["cancelled"] = sess.cancel()
            elif kind in ("done", "failed", "pending"):
                result.update(outcome=kind, detail=data)
                return
        result["outcome"] = "timeout"

    if mode in ("listen", "listen_resolve"):
        done = threading.Event()

        def on_channel(ch):
            try:
                hello, v = proto.read_first_hello(ch)
                if mode == "listen_resolve":
                    result["settled"] = proto.resolve_session(ch, ident, me, first_hello=hello, validator=v)
                else:
                    run(proto.TradeSession(wrap(ch), ident, me, "b", first_hello=hello, validator=v))
            except Exception as e:
                result["error"] = repr(e)
            finally:
                done.set()

        listener = lan.Listener(ident, on_channel, host="127.0.0.1")
        tmp = rendezvous + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{listener.port} {ident.fingerprint}")
        os.rename(tmp, rendezvous)
        done.wait(90)
    else:
        deadline = time.time() + 30
        while not os.path.exists(rendezvous) and time.time() < deadline:
            time.sleep(0.05)
        port, fp = open(rendezvous).read().split()
        if mode == "connect_wrong_fp":
            try:
                lan.connect(ident, "127.0.0.1", int(port), "0" * 64)
                result["outcome"] = "connected"
            except lan.ChannelClosed as e:
                result["outcome"] = "refused"
                result["detail"] = str(e)
        else:
            ch = lan.connect(ident, "127.0.0.1", int(port), fp)
            if mode == "connect_resolve":
                result["settled"] = proto.resolve_session(ch, ident, me)
            else:
                run(proto.TradeSession(wrap(ch), ident, me, "a", my_offer=offer))
    time.sleep(0.3)
    print(json.dumps(result, default=str))


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.homes = {who: os.path.join(sandbox, who, "home") for who in ("ash", "misty")}
        self.failures = 0

    def check(self, desc, ok, detail=""):
        if ok:
            print(f"PASS {desc}")
        else:
            self.failures += 1
            print(f"FAIL {desc}" + (f" :: {detail}" if detail else ""))

    def app(self, who):
        return os.path.join(self.homes[who], ".local/share/poketerm")

    def install(self):
        for who, home in self.homes.items():
            os.makedirs(home, exist_ok=True)
            env = dict(os.environ, HOME=home, POKETERM_REAL_HOME=home, POKETERM_ZSHRC=os.path.join(home, ".zshrc"))
            r = subprocess.run(["bash", os.path.join(REPO, "install.sh")], env=env, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(f"install failed for {who}: {r.stdout[-500:]}{r.stderr[-500:]}")

    def set_pokedex(self, who, lines, keep_history=False):
        app = self.app(who)
        dex = os.path.join(app, "pokedex.txt")
        if os.path.exists(dex):
            os.chmod(dex, 0o644)
        text = "".join(l + "\n" for l in lines)
        with open(dex, "w") as f:
            f.write(text)
        os.chmod(dex, 0o444)
        with open(dex + ".sha256", "w") as f:
            f.write(f"{hashlib.sha256(text.encode()).hexdigest()}  {dex}\n")
        trade = os.path.join(app, "trade")
        if not keep_history:
            for n in ("trades.log", "known_trainers.json", "inflight.json", "pokedex.staged"):
                try:
                    os.unlink(os.path.join(trade, n))
                except FileNotFoundError:
                    pass

    def pokedex(self, who):
        with open(os.path.join(self.app(who), "pokedex.txt")) as f:
            return [l.strip() for l in f if l.strip()]

    def hash_ok(self, who):
        dex = os.path.join(self.app(who), "pokedex.txt")
        recorded = open(dex + ".sha256").read().split()[0]
        return hashlib.sha256(open(dex, "rb").read()).hexdigest() == recorded

    def trade(self, ash_gives, misty_gives, ash_fault="none", misty_fault="none",
              ash_mode="connect", misty_mode="listen", flood=False):
        rv = os.path.join(self.sandbox, f"rv-{time.time_ns()}")
        procs = {}
        junk = []
        for who, mode, give, fault in (("misty", misty_mode, misty_gives, misty_fault),
                                       ("ash", ash_mode, ash_gives, ash_fault)):
            if who == "ash" and flood:
                junk = self.flood(rv)
            env = dict(os.environ, HOME=self.homes[who])
            procs[who] = subprocess.Popen([self.python(who), os.path.abspath(__file__), "party", mode, give, rv, fault],
                                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out = {}
        for s_ in junk:
            try:
                s_.close()
            except OSError:
                pass
        for who, p in procs.items():
            try:
                stdout, stderr = p.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                p.kill()
                stdout, stderr = p.communicate()
            line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
            try:
                out[who] = json.loads(line) if line else {"outcome": f"exit {p.returncode}"}
            except ValueError:
                out[who] = {"outcome": f"bad output {line[:200]}"}
            out[who]["exit"] = p.returncode
            out[who]["stderr"] = stderr[-400:]
        return out

    def flood(self, rv):
        """Junk, oversized and idle connections to Misty's trade port."""
        import socket, struct
        deadline = time.time() + 30
        while not os.path.exists(rv) and time.time() < deadline:
            time.sleep(0.05)
        port = int(open(rv).read().split()[0])
        socks = []
        for i in range(12):
            s_ = socket.create_connection(("127.0.0.1", port))
            if i % 3 == 0:
                s_.sendall(b"GET / HTTP/1.1\r\n\r\n")           # not poketerm
            elif i % 3 == 1:
                s_.sendall(struct.pack(">I", 50_000_000))          # claims a huge message
            socks.append(s_)                                       # every third: silent
        return socks

    def settle(self):
        """The two trainers meet again on the trade page."""
        return self.trade("x", "x", ash_mode="connect_resolve", misty_mode="listen_resolve")

    def catch(self, who):
        home = self.homes[who]
        env = {"HOME": home, "USER": "tester", "PATH": f"{home}/.local/bin:{os.environ['PATH']}",
               "TERM": "xterm-256color", "LANG": "en_US.UTF-8", "POKETERM_NO_UPDATE_CHECK": "1"}
        r = subprocess.run(["zsh", os.path.join(self.app(who), "poketerm")], env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    # -- scenarios ---------------------------------------------------------
    def run_all(self):
        self.install()
        # Ask the Python poketerm will really use (its own, or the system one).
        r = subprocess.run([self.python("ash"), "-c", "import trade_store as s; print(s.check_requirements() or '')"],
                           cwd=self.app("ash"), env=dict(os.environ, HOME=self.homes["ash"]),
                           capture_output=True, text=True)
        if r.stdout.strip() or r.returncode:
            print(f"SKIP all trading tests: {r.stdout.strip() or r.stderr.strip()[-200:]}")
            return 0
        ash0, misty0 = ["bulbasaur 1", "pikachu 2", "eevee (shiny) 1"], ["squirtle 1", "staryu 3"]

        # 1. A normal trade
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("pikachu", "staryu")
        self.check("trade completes on both sides", r["ash"].get("outcome") == "done" and r["misty"].get("outcome") == "done", r)
        self.check("both trainers see the same pairing code", r["ash"].get("code") and r["ash"].get("code") == r["misty"].get("code"), r)
        self.check("Ash loses one Pikachu and gets Staryu",
                   self.pokedex("ash") == ["bulbasaur 1", "pikachu 1", "staryu 1", "eevee (shiny) 1"], self.pokedex("ash"))
        self.check("Misty loses one Staryu and gets Pikachu",
                   self.pokedex("misty") == ["squirtle 1", "pikachu 1", "staryu 2"], self.pokedex("misty"))
        self.check("both Pokedex checksums are valid after the trade", self.hash_ok("ash") and self.hash_ok("misty"))
        rc, out = self.catch("ash")
        self.check("catching still works after a trade", rc == 0 and "modified outside" not in out, out[-300:])
        # The Staryu Ash just received (his only one) can be traded on straight away.
        before_catch = [l for l in self.pokedex("ash") if not l.startswith("staryu ")]
        self.set_pokedex("ash", ["bulbasaur 1", "pikachu 1", "staryu 1"], keep_history=True)
        self.set_pokedex("misty", ["squirtle 1", "pikachu 1", "staryu 2"], keep_history=True)
        r = self.trade("staryu", "squirtle")
        self.check("a Pokemon received in a trade can be traded on",
                   r["ash"].get("outcome") == "done" and "staryu 1" not in self.pokedex("ash")
                   and "squirtle 1" in self.pokedex("ash") and "staryu 3" in self.pokedex("misty"),
                   (r["ash"].get("outcome"), self.pokedex("ash"), self.pokedex("misty")))

        # 2. Trading away the last one removes it; shiny stays shiny
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("eevee:shiny", "squirtle")
        self.check("shiny trade: Ash's last shiny Eevee is gone, Misty has it as shiny",
                   "eevee (shiny) 1" not in self.pokedex("ash") and "eevee (shiny) 1" in self.pokedex("misty")
                   and "squirtle 1" in self.pokedex("ash") and "squirtle 1" not in self.pokedex("misty"),
                   (self.pokedex("ash"), self.pokedex("misty")))

        # 3. Declined / cancelled trades change nothing
        # (cancelling only works before the trade starts, i.e. while the other
        # trainer hasn't confirmed yet - so the other one confirms slowly)
        for fault_who, fault in (("misty", "decline"), ("ash", "cancel_after_confirm"), ("misty", "cancel_after_confirm")):
            self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
            other = "ash" if fault_who == "misty" else "misty"
            r = self.trade("pikachu", "staryu", **{f"{fault_who}_fault": fault, f"{other}_fault": "slow_confirm"})
            self.check(f"{fault} ({fault_who}): nothing traded on either side",
                       self.pokedex("ash") == ash0 and self.pokedex("misty") == misty0
                       and r["ash"].get("outcome") == "failed" and r["misty"].get("outcome") == "failed",
                       (r, self.pokedex("ash"), self.pokedex("misty")))

        # 4. Offering a Pokemon you don't have
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("mewtwo", "staryu")
        self.check("offering a Pokemon you don't own: refused, nothing changes, nothing left pending",
                   self.pokedex("ash") == ash0 and self.pokedex("misty") == misty0
                   and not self.pending("misty") and r["misty"].get("outcome") == "failed", (r, self.pokedex("misty")))
        # ...and the other order: the one who can't complete confirms first
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("mewtwo", "staryu", misty_fault="slow_confirm")
        self.check("offering a Pokemon you don't own (confirming first): nothing changes",
                   self.pokedex("ash") == ash0 and self.pokedex("misty") == misty0 and not self.pending("misty"),
                   (r, self.pokedex("misty")))
        # A cancel that comes too late is refused, and the trade completes on both sides
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("pikachu", "staryu", ash_fault="cancel_after_confirm", misty_fault="none")
        traded = (self.pokedex("ash") == ["bulbasaur 1", "pikachu 1", "staryu 1", "eevee (shiny) 1"]
                  and self.pokedex("misty") == ["squirtle 1", "pikachu 1", "staryu 2"])
        unchanged = self.pokedex("ash") == ash0 and self.pokedex("misty") == misty0
        self.check("a cancel racing the other trainer's confirm: either both traded or neither",
                   (traded and r["ash"].get("cancelled") is False) or (unchanged and r["ash"].get("cancelled") is True),
                   (r["ash"].get("cancelled"), self.pokedex("ash"), self.pokedex("misty")))

        # 5. Crashes at every step: never lost, never duplicated, settles when they meet again
        cases = [
            ("ash", "die_before_confirm", "unchanged"),
            ("ash", "die_after_confirm", "unchanged"),
            ("misty", "die_after_confirm", "unchanged"),
            ("ash", "die_before_ready", "traded"),
            ("misty", "die_before_ready", "traded"),
            ("ash", "die_after_ready", "traded"),
            ("misty", "die_after_ready", "traded"),
        ]
        for who, fault, expect in cases:
            self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
            self.trade("pikachu", "staryu", **{f"{who}_fault": fault})
            before = (self.pokedex("ash"), self.pokedex("misty"))
            total_before = sum(int(l.split()[-1]) for l in before[0] + before[1])
            original = sum(int(l.split()[-1]) for l in ash0 + misty0)
            self.check(f"{who} crashes ({fault}): no Pokemon created from nothing",
                       total_before <= original, before)
            self.settle()
            a, m = self.pokedex("ash"), self.pokedex("misty")
            traded = (a == ["bulbasaur 1", "pikachu 1", "staryu 1", "eevee (shiny) 1"]
                      and m == ["squirtle 1", "pikachu 1", "staryu 2"])
            unchanged = a == ash0 and m == misty0
            self.check(f"{who} crashes ({fault}): after meeting again the trade is {expect} on both sides",
                       traded if expect == "traded" else unchanged, (a, m))
            self.check(f"{who} crashes ({fault}): checksums valid, nothing left pending",
                       self.hash_ok("ash") and self.hash_ok("misty") and not self.pending("ash") and not self.pending("misty"))

        # 6. Five trades a day
        self.set_pokedex("ash", ["pikachu 10"]); self.set_pokedex("misty", ["staryu 10"])
        outcomes = [self.trade("pikachu", "staryu")["ash"].get("outcome") for _ in range(6)]
        self.check("5 trades allowed per day, the 6th is refused", outcomes == ["done"] * 5 + ["failed"], outcomes)
        self.check("after the limit: exactly 5 traded each way",
                   self.pokedex("ash") == ["pikachu 5", "staryu 5"] and self.pokedex("misty") == ["pikachu 5", "staryu 5"],
                   (self.pokedex("ash"), self.pokedex("misty")))

        # 7. Tampering
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        dex = os.path.join(self.app("ash"), "pokedex.txt")
        os.chmod(dex, 0o644)
        with open(dex, "a") as f:
            f.write("mew 99\n")
        r = self.trade("pikachu", "staryu")
        self.check("hand-edited Pokedex: trade refused, other trainer unchanged",
                   r["ash"].get("outcome") == "failed" and self.pokedex("misty") == misty0, (r["ash"], self.pokedex("misty")))
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        self.trade("pikachu", "staryu")
        log = os.path.join(self.app("ash"), "trade", "trades.log")
        lines = open(log).read().splitlines()
        lines[0] = lines[0].replace('"pikachu"', '"mew"')
        with open(log, "w") as f:
            f.write("\n".join(lines) + "\n")
        r = self.trade("pikachu", "staryu")
        self.check("edited trade history: trading refused", r["ash"].get("outcome") == "failed", r["ash"])

        # 7b. Junk connections don't stop a real trade
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("pikachu", "staryu", flood=True)
        self.check("12 junk/oversized/silent connections during a trade: the real trade still completes",
                   r["ash"].get("outcome") == "done" and r["misty"].get("outcome") == "done", r)

        # 8. Identity checks
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("pikachu", "staryu", ash_mode="connect_wrong_fp")
        self.check("connecting to a trainer whose identity doesn't match is refused",
                   r["ash"].get("outcome") == "refused", r["ash"])

        # 9. Trades and new terminals at the same time
        self.set_pokedex("ash", ["pikachu 3"]); self.set_pokedex("misty", ["staryu 3"])
        catches = []
        threads = [threading.Thread(target=lambda: catches.append(self.catch("ash"))) for _ in range(15)]
        for t in threads:
            t.start()
        r = self.trade("pikachu", "staryu")
        for t in threads:
            t.join()
        total = sum(int(l.split()[-1]) for l in self.pokedex("ash"))
        # 3 Pokemon + 15 catches, minus 1 traded away, plus 1 received = 18
        self.check("15 terminals catching during a trade: every catch and the trade recorded",
                   r["ash"].get("outcome") == "done" and total == 18,
                   (r["ash"].get("outcome"), total, self.pokedex("ash")))
        self.check("...with a valid checksum and no catch refused", self.hash_ok("ash") and all(rc == 0 for rc, _ in catches),
                   [o[-200:] for rc, o in catches if rc])

        # 10. Interrupted write is repaired by the next terminal
        self.set_pokedex("ash", ash0)
        trade_dir = os.path.join(self.app("ash"), "trade")
        staged = os.path.join(trade_dir, "pokedex.staged")
        with open(staged, "w") as f:
            f.write("mew 1\n")
        with open(os.path.join(trade_dir, "inflight.json"), "w") as f:
            json.dump({"trade_id": "f" * 64, "event": "committed", "new_hash": hashlib.sha256(b"mew 1\n").hexdigest()}, f)
        rc, out = self.catch("ash")
        self.check("a half-finished write with no journal entry is discarded, not applied",
                   rc == 0 and "mew 1" not in self.pokedex("ash") and self.hash_ok("ash")
                   and not os.path.exists(staged), (rc, out[-200:], self.pokedex("ash")))
        # 12. Updating poketerm keeps the trading identity and history
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        self.trade("pikachu", "staryu")
        trade_dir = os.path.join(self.app("ash"), "trade")
        before = {n: open(os.path.join(trade_dir, n), "rb").read() for n in ("identity.crt", "identity.key", "trades.log")}
        home = self.homes["ash"]
        env = dict(os.environ, HOME=home, POKETERM_REAL_HOME=home, POKETERM_ZSHRC=os.path.join(home, ".zshrc"))
        r = subprocess.run(["bash", os.path.join(REPO, "install.sh"), "--update"], env=env, capture_output=True, text=True)
        after = {n: open(os.path.join(trade_dir, n), "rb").read() for n in before}
        self.check("update keeps the trading identity and trade history", r.returncode == 0 and before == after,
                   r.stdout[-300:])
        backups = sorted(os.listdir(os.path.join(self.app("ash"), "backups")))
        key = os.path.join(self.app("ash"), "backups", backups[-1], "trade", "identity.key")
        self.check("update backup includes trade data, with the private key still private",
                   os.path.exists(key) and (os.stat(key).st_mode & 0o077) == 0, key)

        # 11. The real screens, two windows, found automatically and by address
        for by_address in (False, True):
            how = "typed address" if by_address else "found on the network"
            self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
            f = ui_trade(self, by_address)
            if not by_address and "[ >] Misty" in f.get("error", ""):     # Misty never showed up at all
                print(f"SKIP trade screens, {how}: this network doesn't allow discovery (multicast/broadcast)")
                continue
            ok = (f.get("complete") and f.get("code_a") == f.get("code_b") and f.get("animations")
                  and f.get("exit_Ash") == 0 and f.get("exit_Misty") == 0
                  and self.pokedex("ash") == ["bulbasaur 1", "pikachu 1", "staryu 1", "eevee (shiny) 1"]
                  and self.pokedex("misty") == ["squirtle 1", "pikachu 1", "staryu 2"])
            self.check(f"trade screens ({how}): codes match, animations play, both Pokedexes updated", ok,
                       {k: v for k, v in f.items() if k != "screen"} if ok else f)
        return self.failures

    def python(self, who):
        """The Python poketerm itself uses for this trainer (its own, if installed)."""
        own = os.path.join(self.homes[who], ".local/share/poketerm-python/bin/python3")
        return own if os.access(own, os.X_OK) else sys.executable

    def pending(self, who):
        r = subprocess.run([self.python(who), "-c", "import trade_store as s; print(len(s.pending_trades()))"],
                           cwd=self.app(who), env=dict(os.environ, HOME=self.homes[who]), capture_output=True, text=True)
        return r.stdout.strip() != "0"


# ---------------------------------------------------------------------------
# Two real Pokedex windows, driven through pseudo-terminals like a person would
# ---------------------------------------------------------------------------
class Term:
    ANSI = None
    ALL = []

    def __init__(self, name, home):
        import pty, fcntl, termios, struct, re
        Term.ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]")
        self.name, self.buf = name, b""
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(HOME=home, TERM="xterm-256color", LANG=os.environ.get("LANG", "C.UTF-8"),
                              PATH=f"{home}/.local/bin:{os.environ['PATH']}")
            os.execvp("pokedex", ["pokedex"])
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 100, 0, 0))
        Term.ALL.append(self)

    def pump(self, t=0.1):
        import select
        end = time.time() + t
        while time.time() < end:
            fds = {x.fd: x for x in Term.ALL if x.fd is not None}
            if not fds:
                return
            r, _, _ = select.select(list(fds), [], [], 0.05)
            for fd in r:
                try:
                    fds[fd].buf += os.read(fd, 65536)
                except OSError:
                    fds[fd].fd = None

    def text(self):
        return Term.ANSI.sub(b"", self.buf).decode("utf-8", "replace")

    def mark(self):
        self.buf = b""

    def wait(self, pattern, timeout=25):
        import re
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.2)
            m = re.search(pattern, self.text())
            if m:
                return m
        raise TimeoutError(f"{self.name}: no {pattern!r} on screen")

    def send(self, keys):
        os.write(self.fd, keys.encode())

    def close(self):
        """Stop the window. Our end of its terminal is closed *before* waiting:
        a process can't finish exiting while its screen output is unread, so
        waiting first could hang forever. Never waits more than 5 seconds."""
        try:
            os.kill(self.pid, 9)
        except OSError:
            pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        end = time.time() + 5
        while time.time() < end:
            try:
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    break
            except ChildProcessError:
                break
            time.sleep(0.05)
        if self in Term.ALL:
            Term.ALL.remove(self)


def ui_trade(runner, by_address):
    """Ash offers Pikachu to Misty through the real screens. Returns a dict of findings."""
    import re
    found = {}
    for who, name in (("ash", "Ash"), ("misty", "Misty")):
        path = os.path.join(runner.app(who), "trainer.json")
        data = json.load(open(path))
        data["name"] = name
        json.dump(data, open(path, "w"))
    A, B = Term("Ash", runner.homes["ash"]), Term("Misty", runner.homes["misty"])
    try:
        for t in (A, B):
            t.wait(r"Page 1/"); t.mark(); t.send("t")
        for t in (A, B):
            t.wait(r"Trades left today: 5/5")
        A.wait(r"> Bulbasaur")
        A.mark(); A.send("\x1b[B"); A.wait(r"Selected: Pikachu")
        A.mark(); A.send("\r"); A.wait(r"Trainers nearby")
        if by_address:
            B.mark(); B.send("\r"); addr = B.wait(r"Your address: [0-9.]+:(\d+)").group(1)
            B.send("b"); B.wait(r"Your Pokemon:")
            A.mark(); A.send("c"); A.wait(r"Connect by address")
            A.send(f"127.0.0.1:{addr}"); A.mark(); A.send("\r")
        else:
            # Other trainers on the network may be listed too: move down to Misty.
            A.wait(r"│ [ >] Misty", 15)
            for _ in range(10):
                if re.search(r"> Misty", A.text()[-4000:]):
                    break
                A.mark(); A.send("\x1b[B"); A.pump(0.6)
            A.wait(r"> Misty", 5)
            A.mark(); A.send("\r")
        found["code_a"] = A.wait(r"Pairing code: (\d{3} \d{3})").group(1)
        B.wait(r"Ash wants to trade with you"); B.wait(r"offering: Pikachu")
        found["code_b"] = B.wait(r"Pairing code: (\d{3} \d{3})").group(1)
        B.mark(); A.mark(); B.send("y")
        B.wait(r"You get:\s+Pikachu")
        B.send("\x1b[B"); B.mark(); B.send("\r")
        A.wait(r"You get:\s+Staryu")
        A.mark(); A.send("y"); B.wait(r"Ash confirmed")
        B.mark(); B.send("y")
        # Each side plays two ~2.5s animations; allow for slow or busy machines.
        A.wait(r"Misty sent you Staryu!", 45); B.wait(r"Ash sent you Pikachu!", 45)
        found["animations"] = True
        for t in (A, B):
            t.wait(r"Trade complete!", 45)
        A.wait(r"Trades left today: 4/5")
        found["complete"] = True
        for t in (A, B):
            t.mark(); t.send("q"); t.wait(r"Page 1/"); t.send("q")
        for t in (A, B):
            end = time.time() + 10
            while time.time() < end:
                t.pump(0.2)
                pid, st = os.waitpid(t.pid, os.WNOHANG)
                if pid:
                    found[f"exit_{t.name}"] = os.WEXITSTATUS(st)
                    break
    except (TimeoutError, OSError) as e:
        found["error"] = str(e)
        found["pokedexes"] = (runner.pokedex("ash"), runner.pokedex("misty"))
        found["screen"] = (A.text()[-1500:], B.text()[-1500:])
    finally:
        for t in (A, B):
            if t in Term.ALL:
                t.close()
    return found


def unit_tests():
    """Protocol checks that don't need two processes."""
    import tempfile
    home = tempfile.mkdtemp(prefix="poketerm-unit.")
    os.environ["HOME"] = home
    sys.path.insert(0, os.path.join(REPO, "files", "current"))
    import importlib
    import trade_store as store
    store.GEN_DIR = __import__("pathlib").Path(REPO, "files", "gen_files")
    import trade_protocol as proto
    failures = 0

    def check(desc, ok):
        nonlocal failures
        print(("PASS " if ok else "FAIL ") + desc)
        failures += 0 if ok else 1

    v = proto.Validator()
    good = proto.message("offer", pokemon={"name": "pikachu", "shiny": False})
    check("valid message accepted", v.check(dict(good))["pokemon"]["name"] == "pikachu")
    try:
        v.check(dict(good)); check("replayed message rejected", False)
    except proto.ProtocolError:
        check("replayed message rejected", True)
    for desc, msg in [
        ("old message rejected", dict(proto.message("offer", pokemon=None), ts=time.time() - 3600)),
        ("message from the future rejected", dict(proto.message("offer", pokemon=None), ts=time.time() + 3600)),
        ("unknown Pokemon rejected", proto.message("offer", pokemon={"name": "agumon", "shiny": False})),
        ("Pokemon name with escape codes rejected", proto.message("offer", pokemon={"name": "pikachu\x1b[2J", "shiny": False})),
        ("extra fields rejected", dict(proto.message("offer", pokemon=None), evil=1)),
        ("unknown message type rejected", dict(proto.message("offer", pokemon=None), type="give_me_everything")),
        ("wrong protocol version rejected", dict(proto.message("offer", pokemon=None), v=99)),
        ("non-boolean shiny rejected", proto.message("offer", pokemon={"name": "pikachu", "shiny": "yes"})),
    ]:
        try:
            proto.Validator().check(msg); check(desc, False)
        except proto.ProtocolError:
            check(desc, True)
    check("trainer names can't inject terminal codes", proto.clean_name("Ash\x1b[31m\x1b]0;pwned\x07") == "Ash[31m]0;pwned")
    check("pairing code is the same for both sides' view",
          proto.pairing_code("a" * 64, "b" * 64, "1" * 32, "2" * 32) == proto.pairing_code("a" * 64, "b" * 64, "1" * 32, "2" * 32))
    check("pairing code changes if either identity changes",
          proto.pairing_code("a" * 64, "b" * 64, "1" * 32, "2" * 32) != proto.pairing_code("a" * 64, "c" * 64, "1" * 32, "2" * 32))
    # The two crypto paths (cryptography, and the openssl command used on a
    # system Python) must understand each other's identities and signatures.
    if store.HAVE_CRYPTOGRAPHY and store.openssl_cmd():
        os.makedirs(store.TRADE_DIR, exist_ok=True)
        ident_c = store.load_identity()                       # made with cryptography
        for n in (store.KEY_FILE, store.CERT_FILE):
            n.rename(str(n) + ".c")
        store._create_identity_openssl()
        ident_o = store.Identity(store.CERT_FILE.read_text())  # made with openssl
        data = b"the exact trade terms"
        # put the cryptography-made identity back to sign with it
        for n in (store.KEY_FILE, store.CERT_FILE):
            n.rename(str(n) + ".o")
        for n in (store.KEY_FILE, store.CERT_FILE):
            os.rename(str(n) + ".c", n)
        store._key_cache.clear()
        sig_c = store.sign(ident_c, data)
        check("openssl verifies a signature made with cryptography",
              store._verify_openssl(ident_c.cert_pem, __import__("base64").b64decode(sig_c), data))
        class O:                       # the openssl-made identity, signed with openssl
            key_path, cert_pem = str(store.KEY_FILE) + ".o", ident_o.cert_pem
        sig_o = store._sign_openssl(O, data)
        check("cryptography verifies a signature made with openssl", store.verify(ident_o.cert_pem, data, sig_o))
        check("a signature doesn't verify for different terms", not store.verify(ident_o.cert_pem, b"other terms", sig_o))
        check("a signature doesn't verify with another trainer's identity", not store.verify(ident_c.cert_pem, data, sig_o))
    else:
        print("SKIP crypto interoperability: needs both cryptography and the openssl command")

    # You can't trade with yourself: you never see your own announcements,
    # and connecting to your own trade port is refused.
    import trade_lan as lan
    os.makedirs(store.TRADE_DIR, exist_ok=True)
    for n in (store.KEY_FILE, store.CERT_FILE):
        if n.exists():
            n.unlink()
    me = store.load_identity()
    listener = lan.Listener(me, lambda ch: ch.close(), host="127.0.0.1")
    disc = lan.Discovery(me, "Ash", listener.port)
    time.sleep(2.5)
    check("you never appear in your own list of nearby trainers",
          all(p["fp"] != me.fingerprint for p in disc.nearby()))
    try:
        lan.connect(me, "127.0.0.1", listener.port, timeout=5)
        check("connecting to your own trade page is refused", False)
    except (lan.ChannelClosed, OSError):
        check("connecting to your own trade page is refused", True)
    if disc.interfaces:
        check("finds this computer's networks to announce on",
              all(len(i) == 3 and not i[1].startswith("127.") for i in disc.interfaces))
    else:
        print("SKIP network detection: this computer has no network")
    disc.close(); listener.close()

    # Keys arriving in one burst (a held-down arrow, or arrow then Enter typed
    # fast) must each be read on their own, in the Pokedex as in trading.
    try:
        got = _key_burst()
    except OSError:
        got = ""
    if got.startswith("UNSUPPORTED"):
        print("SKIP key bursts: this environment has no terminal support (e.g. CPU emulation)")
    else:
        check("a burst of arrow keys and Enter is read key by key (no lost or garbled keys)",
              got == json.dumps(["\x1b[B", "\x1b[B", "\x1b[B", "\r", "\x1b[A", "q"]))
    shutil.rmtree(home, ignore_errors=True)
    return failures


def _key_burst():
    """Send keys in one burst to readchar in a pseudo-terminal; return what it read (JSON)."""
    import pty, select
    pid, fd = pty.fork()
    if pid == 0:
        import readchar
        keys = []
        if not hasattr(readchar, "cbreak"):     # older readchar: read in the same burst
            sys.stdout.write("READY\n"); sys.stdout.flush()
            while len(keys) < 6:
                keys.append(readchar.readkey())
            sys.stdout.write("KEYS " + json.dumps(keys) + "\n"); sys.stdout.flush()
            os._exit(0)
        try:
            with readchar.cbreak():
                sys.stdout.write("READY\n"); sys.stdout.flush()
                while len(keys) < 6:
                    keys.append(readchar.readkey())
        except (OSError, __import__("termios").error) as e:   # e.g. CPU emulation without terminal support
            sys.stdout.write(f"READY\nKEYS UNSUPPORTED {e}\n"); sys.stdout.flush()
            os._exit(0)
        sys.stdout.write("KEYS " + json.dumps(keys) + "\n"); sys.stdout.flush()
        os._exit(0)
    buf, end = b"", time.time() + 10
    while b"READY" not in buf and time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            buf += os.read(fd, 100)
    os.write(fd, b"\x1b[B\x1b[B\x1b[B\r\x1bOAq")
    while b"KEYS" not in buf.split(b"READY")[-1] or not buf.endswith(b"\n"):
        if time.time() > end or not select.select([fd], [], [], 0.2)[0]:
            if time.time() > end:
                break
            continue
        try:
            buf += os.read(fd, 1000)
        except OSError:
            break
    try:
        os.kill(pid, 9)         # already exited if it worked; stuck if keys were garbled
    except OSError:
        pass
    os.waitpid(pid, 0)
    return buf.decode(errors="replace").split("KEYS ")[-1].strip() if b"KEYS" in buf else ""


if __name__ == "__main__":
    if sys.argv[1:2] == ["party"]:
        party(sys.argv[2:])
    elif sys.argv[1:2] == ["unit"]:
        sys.exit(1 if unit_tests() else 0)
    else:
        sys.exit(1 if Runner(sys.argv[1]).run_all() else 0)
