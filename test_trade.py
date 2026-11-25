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
    details = None
    pick = os.environ.get("POKETERM_TEST_PICK")          # offer this exact Pokemon (by nature)
    if pick:
        import collection
        with store.pokedex_lock():
            recs = [r for r in collection.of_kind(offer["name"], offer["shiny"]) if r.get("nature") == pick]
        details = collection.for_trade(recs[0]) if recs else None
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
                        sess.set_offer(offer, details)
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
                sess = proto.TradeSession(wrap(ch), ident, me, "a", my_offer=offer)
                sess.my_details = details
                run(sess)
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
            try:
                os.unlink(os.path.join(app, "pokemon.log"))
            except FileNotFoundError:
                pass
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
              ash_mode="connect", misty_mode="listen", flood=False, ash_pick=None):
        rv = os.path.join(self.sandbox, f"rv-{time.time_ns()}")
        procs = {}
        junk = []
        for who, mode, give, fault in (("misty", misty_mode, misty_gives, misty_fault),
                                       ("ash", ash_mode, ash_gives, ash_fault)):
            if who == "ash" and flood:
                junk = self.flood(rv)
            env = dict(os.environ, HOME=self.homes[who])
            env.pop("POKETERM_TEST_PICK", None)
            if who == "ash" and ash_pick:
                env["POKETERM_TEST_PICK"] = ash_pick
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
        # A private discovery channel for this run: test trainers must never see,
        # or be seen by, real trainers on the network.
        os.environ["POKETERM_DISCOVERY_PORT"] = str(47400 + os.getpid() % 500)
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
        got = self.achievements("ash")
        self.check("after a first real trade: 'Nice doing business with you' unlocked",
                   "Nice doing business with you" in got, got)
        # The Staryu Ash just received (his only one) can be traded on straight away.
        before_catch = [l for l in self.pokedex("ash") if not l.startswith("staryu ")]
        self.set_pokedex("ash", ["bulbasaur 1", "pikachu 1", "staryu 1"], keep_history=True)
        self.set_pokedex("misty", ["squirtle 1", "pikachu 1", "staryu 2"], keep_history=True)
        r = self.trade("staryu", "squirtle")
        got = self.achievements("misty")
        self.check("Misty got her traded-away Staryu back: 'Boomerang' unlocked", "Boomerang" in got, got)
        self.check("a Pokemon received in a trade can be traded on",
                   r["ash"].get("outcome") == "done" and "staryu 1" not in self.pokedex("ash")
                   and "squirtle 1" in self.pokedex("ash") and "staryu 3" in self.pokedex("misty"),
                   (r["ash"].get("outcome"), self.pokedex("ash"), self.pokedex("misty")))

        # 2. Trading away the last one removes it; shiny stays shiny
        self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
        r = self.trade("eevee:shiny", "squirtle")
        self.check("trading a shiny: 'That's very generous!' for the giver, 'Special Delivery' for the receiver",
                   "That's very generous!" in self.achievements("ash") and "Special Delivery" in self.achievements("misty"),
                   (self.achievements("ash"), self.achievements("misty")))
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

        # 5b. Each Pokemon's story travels with it, and comes back if cancelled
        self.set_pokedex("ash", ["pikachu 2"]); self.set_pokedex("misty", ["staryu 2"])
        seeded = self.collection("ash", """
import random
with trade_store.pokedex_lock():
    collection.reconcile()                       # the 2 Pikachu appear (from before 0.0.8)
# give them known natures, like two real catches would have
recs = collection.of_kind("pikachu")
events = []
for rec, nature in zip(recs, ("Jolly", "Timid")):
    collection._append({"e": "released", "id": rec["id"]},
                       {"e": "caught", "id": rec["id"], "pokemon": {"name": "pikachu", "shiny": False,
                        "caught_at": "2026-11-03T09:41:00+00:00", "nature": nature, "catch_no": 1,
                        "original_trainer": "Ash", "history": []}})
print(json.dumps(sorted(r["nature"] for r in collection.of_kind("pikachu"))))""")
        self.check("older Pokemon get records; a catch's details are stored", seeded == ["Jolly", "Timid"], seeded)
        r = self.trade("pikachu", "staryu", ash_pick="Timid")
        ash_left = self.collection("ash", "print(json.dumps([r['nature'] for r in collection.of_kind('pikachu')]))")
        got = self.collection("misty", "print(json.dumps(collection.of_kind('pikachu')))")
        self.check("the exact Pokemon chosen is the one that leaves (Timid goes, Jolly stays)",
                   r["ash"].get("outcome") == "done" and ash_left == ["Jolly"], (r["ash"].get("outcome"), ash_left))
        rec = got[0] if isinstance(got, list) and got else {}
        self.check("its story arrives with it: nature, catch date, original trainer, and this trade",
                   rec.get("nature") == "Timid" and rec.get("original_trainer") == "Ash"
                   and rec.get("caught_at", "").startswith("2026-11-03")
                   and (rec.get("history") or [{}])[-1].get("from") == "Ash"
                   and (rec.get("history") or [{}])[-1].get("to") == "Misty", rec)
        # cancelled: Jolly must come back exactly as it was
        self.set_pokedex("ash", ["pikachu 1"], keep_history=True); self.set_pokedex("misty", ["staryu 2"], keep_history=True)
        before = self.collection("ash", "print(json.dumps(collection.of_kind('pikachu')))")
        self.trade("pikachu", "staryu", ash_pick="Jolly", ash_fault="cancel_after_confirm", misty_fault="slow_confirm")
        after = self.collection("ash", "print(json.dumps(collection.of_kind('pikachu')))")
        self.check("a cancelled trade gives back the very same Pokemon, story intact",
                   isinstance(before, list) and before == after and after and after[0]["nature"] == "Jolly", (before, after))

        # 5c. Nicknames and met level travel; favourites are refused
        self.set_pokedex("ash", ["pikachu 1"]); self.set_pokedex("misty", ["staryu 1"])
        self.collection("ash", """
with trade_store.pokedex_lock():
    rec = collection.of_kind("pikachu")[0]
collection._append({"e": "released", "id": rec["id"]},
                   {"e": "caught", "id": rec["id"], "pokemon": {"name": "pikachu", "shiny": False,
                    "caught_at": "2026-11-03T09:41:00+00:00", "nature": "Jolly", "catch_no": 1,
                    "original_trainer": "Ash", "history": [], "met_level": 7}})
collection.set_nickname(rec["id"], "Sparky")
print("{}")""")
        r = self.trade("pikachu", "staryu", ash_pick="Jolly")
        got = self.collection("misty", "print(json.dumps(collection.of_kind('pikachu')))")
        rec = got[0] if isinstance(got, list) and got else {}
        self.check("a nickname and 'met at Lv' travel with the Pokemon",
                   r["ash"].get("outcome") == "done" and rec.get("nickname") == "Sparky" and rec.get("met_level") == 7,
                   (r["ash"].get("outcome"), rec))
        self.set_pokedex("ash", ["pikachu 1"]); self.set_pokedex("misty", ["staryu 1"])
        self.collection("ash", """
with trade_store.pokedex_lock():
    rec = collection.of_kind("pikachu")[0]
collection.set_favourite(rec["id"], True)
print("{}")""")
        r = self.trade("pikachu", "staryu")
        self.check("a ♥ favourite can't be traded: refused, nothing changes on either side",
                   r["ash"].get("outcome") == "failed" and "favourite" in str(r["ash"].get("detail"))
                   and self.pokedex("ash") == ["pikachu 1"] and self.pokedex("misty") == ["staryu 1"]
                   and not self.pending("misty"), (r["ash"].get("detail"), self.pokedex("ash"), self.pokedex("misty")))

        # 6. Five trades a day
        self.set_pokedex("ash", ["pikachu 10"]); self.set_pokedex("misty", ["staryu 10"])
        outcomes = [self.trade("pikachu", "staryu")["ash"].get("outcome") for _ in range(6)]
        self.check("5 trades allowed per day, the 6th is refused", outcomes == ["done"] * 5 + ["failed"], outcomes)
        got = self.achievements("ash")
        self.check("5 trades in a day: 'Regular at the Trade Centre' and 'Busy day at the Trade Centre'",
                   {"Regular at the Trade Centre", "Busy day at the Trade Centre"} <= got, got)
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

        # 10b. Nickname and favourite on a real Pokedex entry page
        self.set_pokedex("ash", ["bulbasaur 1"])
        f = entry_actions(self)
        self.check("entry page: f marks a favourite, n sets a nickname, both saved", f.get("ok"), f)

        # 10b2. The settings page
        f = settings_actions(self)
        self.check("settings page: untick a generation, keep the last one, 'a' for all, turn details off",
                   f.get("ok"), f)
        try:
            os.unlink(os.path.join(self.app("ash"), "settings.conf"))
        except FileNotFoundError:
            pass

        # 10b3. The catch history page
        f = history_actions(self)
        self.check("catch history page: h opens it from the Pokedex, b comes back", f.get("ok"), f)

        # 10c. Search in the real Pokedex
        self.set_pokedex("ash", ash0)
        f = search_actions(self)
        self.check("Pokedex search: / jumps to Mewtwo, switches generation for Pichu, handles no match and Esc",
                   f.get("ok"), f)

        # 11. The real screens, two windows, found automatically and by address
        for by_address in (False, True):
            how = "typed address" if by_address else "found on the network"
            self.set_pokedex("ash", ash0); self.set_pokedex("misty", misty0)
            f = ui_trade(self, by_address)
            if not by_address and "[ >] Misty" in f.get("error", ""):     # Misty never showed up at all
                print(f"SKIP trade screens, {how}: this network doesn't allow discovery (multicast/broadcast)")
                continue
            ok = (f.get("complete") and f.get("code_a") == f.get("code_b") and f.get("animations")
                  and f.get("traded_label") and f.get("picker") and f.get("trade_search")
                  and f.get("exit_Ash") == 0 and f.get("exit_Misty") == 0
                  and self.pokedex("ash") == ["bulbasaur 1", "pikachu 1", "staryu 1", "eevee (shiny) 1"]
                  and self.pokedex("misty") == ["squirtle 1", "pikachu 1", "staryu 2"])
            self.check(f"trade screens ({how}): search, pick which one, codes match, animations, Pokedexes, 'Traded'", ok,
                       {k: v for k, v in f.items() if k != "screen"} if ok else f)
        return self.failures

    def python(self, who):
        """The Python poketerm itself uses for this trainer (its own, if installed)."""
        own = os.path.join(self.homes[who], ".local/share/poketerm-python/bin/python3")
        return own if os.access(own, os.X_OK) else sys.executable

    def collection(self, who, code):
        """Run Python against this trainer's individual-Pokemon records."""
        r = subprocess.run([self.python(who), "-c", "import collection, json, trade_store\n" + code],
                           cwd=self.app(who), env=dict(os.environ, HOME=self.homes[who]), capture_output=True, text=True)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"error": r.stderr[-400:]}

    def achievements(self, who):
        """Run the achievement check for this trainer; return the trading ones they have."""
        code = ("import achievements as a, json; a.check_pokedex_achievements(); "
                "print(json.dumps([h['name'] for h in a.load_trainer()['hall_of_fame'] if h['category'] == 'trade']))")
        r = subprocess.run([self.python(who), "-c", code], cwd=self.app(who),
                           env=dict(os.environ, HOME=self.homes[who]), capture_output=True, text=True)
        try:
            return set(json.loads(r.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            return {"ERROR: " + r.stderr[-300:]}

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


def entry_actions(runner):
    """Open Bulbasaur's entry, press f, then n and type a nickname."""
    import re
    found = {}
    path = os.path.join(runner.app("ash"), "trainer.json")
    data = json.load(open(path)); data["name"] = "Ash"; json.dump(data, open(path, "w"))
    A = Term("Ash", runner.homes["ash"])
    try:
        A.wait(r"Page 1/"); A.mark(); A.send("\r")
        A.wait(r"YOUR BULBASAUR"); A.mark(); A.send("f"); A.wait(r"♥ Favourite")
        A.mark(); A.send("n"); A.wait(r"Nickname:")
        for ch in "Leafy":
            A.send(ch)
        A.pump(0.4); A.mark(); A.send("\r"); A.wait(r'"Leafy"')
        A.send("b"); A.wait(r"Page 1/"); A.send("q")
        saved = runner.collection("ash", "print(json.dumps(collection.of_kind('bulbasaur')))")
        rec = saved[0] if isinstance(saved, list) and saved else {}
        found["saved"] = {k: rec.get(k) for k in ("nickname", "favourite")}
        found["ok"] = rec.get("nickname") == "Leafy" and rec.get("favourite") is True
    except (TimeoutError, OSError) as e:
        found["error"] = str(e); found["screen"] = A.text()[-1200:]
    finally:
        if A in Term.ALL:
            A.close()
    return found


def settings_actions(runner):
    """Use the real settings page: untick Gen 1, try to untick the last one, 'a' for all."""
    found = {}
    conf = os.path.join(runner.app("ash"), "settings.conf")
    try:
        os.unlink(conf)
    except FileNotFoundError:
        pass
    A = Term("Ash", runner.homes["ash"])
    try:
        A.wait(r"Page 1/"); A.mark(); A.send("s"); A.wait(r"Catch Pokemon from")
        A.mark(); A.send("\r"); A.wait(r"Saved")
        found["gen1_off"] = "generations=2,3,4,5,6,7,8" in open(conf).read()
        for g in range(2, 8):                         # untick 2..7: only Gen 8 left
            A.send("\x1b[B"); A.pump(0.15); A.send("\r"); A.pump(0.3)
        A.mark(); A.send("\x1b[B"); A.pump(0.15); A.send("\r")
        A.wait(r"At least one generation has to stay on")
        found["last_kept"] = "generations=8" in open(conf).read()
        A.mark(); A.send("a"); A.wait(r"All 8 generations on")
        found["all_on"] = "generations=1,2,3,4,5,6,7,8" in open(conf).read()
        A.send("\x1b[B"); A.pump(0.15); A.mark(); A.send("\r"); A.wait(r"Saved")
        found["details_off"] = "system_details=off" in open(conf).read()
        A.mark(); A.send("b"); A.wait(r"Page 1/"); A.send("q")
    except (TimeoutError, OSError) as e:
        found["error"] = str(e); found["screen"] = A.text()[-1500:]
    finally:
        if A in Term.ALL:
            A.close()
    found["ok"] = all(found.get(k) for k in ("gen1_off", "last_kept", "all_on", "details_off"))
    return found


def history_actions(runner):
    """Open the real catch history page with h, and come back with b."""
    import re
    found = {}
    A = Term("Ash", runner.homes["ash"])
    try:
        A.wait(r"Page 1/"); A.mark(); A.send("h"); A.wait(r"CATCH HISTORY")
        found["opened"] = True
        found["content"] = bool(re.search(r"No catch times recorded yet|First catch", A.text()))
        A.mark(); A.send("b"); A.wait(r"Page 1/"); found["back"] = True
        A.send("q")
    except (TimeoutError, OSError) as e:
        found["error"] = str(e); found["screen"] = A.text()[-1500:]
    finally:
        if A in Term.ALL:
            A.close()
    found["ok"] = all(found.get(k) for k in ("opened", "content", "back"))
    return found


def search_actions(runner):
    """Use / in the real Pokedex: jump to Mewtwo, to Pichu in Gen 2, a miss, and Esc."""
    found = {}
    A = Term("Ash", runner.homes["ash"])
    try:
        A.wait(r"Page 1/")
        A.mark(); A.send("/"); A.wait(r"Search:")
        for ch in "mewtwo":
            A.send(ch)
        A.pump(0.4); A.mark(); A.send("\r")
        A.wait(r"> ?150\s+Mewtwo|150\s+Mewtwo"); A.wait(r"Page 10/")
        found["mewtwo"] = True
        A.mark(); A.send("/"); A.wait(r"Search:")
        for ch in "pichu":
            A.send(ch)
        A.pump(0.4); A.mark(); A.send("\r"); A.wait(r"GENERATION 2 POKEDEX")
        found["other_generation"] = True
        A.mark(); A.send("/"); A.wait(r"Search:")
        for ch in "zzzz":
            A.send(ch)
        A.wait(r"No Pokemon match"); found["no_match"] = True
        A.mark(); A.send("\x1b"); A.wait(r"GENERATION 2 POKEDEX"); found["escape"] = True
        A.send("q")
    except (TimeoutError, OSError) as e:
        found["error"] = str(e); found["screen"] = A.text()[-1500:]
    finally:
        if A in Term.ALL:
            A.close()
    found["ok"] = all(found.get(k) for k in ("mewtwo", "other_generation", "no_match", "escape"))
    return found


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
            t.wait(r"Trades left today: 5/5"); t.wait(r"Trainers nearby")
        # 1. Find each other
        if by_address:
            addr = B.wait(r"Your address: [0-9.]+:(\d+)").group(1)
            A.mark(); A.send("c"); A.wait(r"Connect by address")
            A.send(f"127.0.0.1:{addr}"); A.mark(); A.send("\r")
        else:
            # Other trainers on the network may be listed too: move down to Misty.
            A.wait(r"[ >] Misty\b", 15)
            for _ in range(10):
                if re.search(r"> Misty", A.text()[-4000:]):
                    break
                A.mark(); A.send("\x1b[B"); A.pump(0.6)
            A.wait(r"> Misty", 5)
            A.mark(); A.send("\r")
        # 2. Pairing code, accept
        found["code_a"] = A.wait(r"Pairing code: (\d{3} \d{3})").group(1)
        B.wait(r"Ash wants to trade with you")
        found["code_b"] = B.wait(r"Pairing code: (\d{3} \d{3})").group(1)
        B.mark(); A.mark(); B.send("y")
        # 3. Both choose (Ash: 2nd = Pikachu, Misty: 2nd = Staryu)
        A.wait(r"choose below"); B.wait(r"choose below")
        # Both have several of the Pokemon they offer, so each is asked which one to send.
        A.mark(); A.send("/"); A.wait(r"FIND A POKEMON TO TRADE")        # search for it
        for ch in "pika":
            A.send(ch)
        A.pump(0.4); A.mark(); A.send("\r"); A.wait(r"> Pikachu")
        found["trade_search"] = True
        A.mark(); A.send("\r"); A.wait(r"Which Pikachu will you send")
        found["picker"] = True
        A.mark(); A.send("\r"); A.wait(r"You give: Pikachu")
        B.send("\x1b[B"); B.mark(); B.send("\r"); B.wait(r"Which Staryu will you send")
        B.mark(); B.send("\r"); B.wait(r"You give: Staryu")
        A.wait(r"You get:\s+Staryu"); B.wait(r"You get:\s+Pikachu")
        A.mark(); A.send("y"); B.wait(r"Ash confirmed")
        B.mark(); B.send("y")
        # Each side plays two ~2.5s animations; allow for slow or busy machines.
        A.wait(r"Misty sent you Staryu!", 45); B.wait(r"Ash sent you Pikachu!", 45)
        found["animations"] = True
        for t in (A, B):
            t.wait(r"Trade complete!", 45)
        A.wait(r"Trades left today: 4/5")
        found["complete"] = True
        # The received Pokemon's Pokedex entry says who it came from.
        for t, dex_no, other in ((A, 120, "Misty"), (B, 25, "Ash")):
            t.mark(); t.send("q"); t.wait(r"Page 1/")
            index = dex_no - 1
            for _ in range(index // 15):
                t.send("\x1b[C"); t.pump(0.15)
            for _ in range(index % 15):
                t.send("\x1b[B"); t.pump(0.1)
            t.mark(); t.send("\r")
            t.wait(rf"Traded: received from {other} on \d+ \w+ \d{{4}}")
            t.mark(); t.send("x"); t.wait(r"Page \d+/")
        found["traded_label"] = True
        for t in (A, B):
            t.mark(); t.send("q")
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


def secrets_hex():
    import secrets
    return secrets.token_hex(8)


def unit_tests():
    """Protocol checks that don't need two processes."""
    import tempfile
    import re
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
    os.environ["POKETERM_DISCOVERY_PORT"] = str(47400 + os.getpid() % 500)
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

    # Individual Pokemon records
    import collection as col
    import trade_store as ts2
    ts2.APP_DIR.mkdir(parents=True, exist_ok=True)
    (ts2.APP_DIR / "pokedex.txt").write_text("bulbasaur 2\npikachu 1\npikachu (shiny) 1\n")
    recs = col.reconcile()
    check("Pokemon from before 0.0.8 appear, one record each", len(recs) == 4 and all(r.get("legacy") for r in recs))
    (ts2.APP_DIR / "pokedex.txt").write_text("bulbasaur 3\npikachu 1\npikachu (shiny) 1\n")
    new = col.record_catch("bulbasaur", False, "Ash", "2026-11-25T21:41:00+00:00")
    check("a catch is recorded with nature, time, trainer and catch number",
          new["nature"] in col.NATURES and new["original_trainer"] == "Ash" and new["catch_no"] == 3
          and col.when_text(new["caught_at"]) == "25 Nov 2026, 9:41pm" and col.time_of_day(new["caught_at"]) == "night")
    check("records match the Pokedex after a catch (no duplicates)", len(col.reconcile()) == 5)
    (ts2.APP_DIR / "pokedex.txt").write_text("bulbasaur 1\npikachu 1\npikachu (shiny) 1\n")
    left = [r for r in col.reconcile() if r["name"] == "bulbasaur"]
    check("removing Pokemon outside poketerm drops records, unknown-date ones first, keeping real catches",
          len(left) == 1 and left[0].get("nature") == new["nature"] and not left[0].get("legacy"))
    nasty = {"name": "pikachu", "shiny": False, "nature": "Evil\x1b[2J", "caught_at": "yesterday",
             "catch_no": "lots", "original_trainer": "Misty\x1b]0;pwned\x07",
             "history": [{"from": "A\x1b[31m", "to": "B", "date": "2026-11-01"}, "junk", {"date": "x"}], "extra": 1}
    clean = col.clean_details(nasty, "pikachu", False)
    check("details from another trainer are cleaned: bad nature/date/number dropped, no terminal codes",
          clean["nature"] is None and clean["caught_at"] is None and clean["catch_no"] is None
          and "\x1b" not in json.dumps(clean) and len(clean["history"]) == 1 and "extra" not in clean)
    check("details for a different Pokemon than the one traded are refused",
          col.clean_details(dict(nasty, name="mew"), "pikachu", False) is None)
    nick_ok = col.clean_nickname("Sparky") == "Sparky" and col.clean_nickname("Mr. Bubbles") == "Mr. Bubbles"
    nick_bad = all(col.clean_nickname(v) is None for v in
                   ("", "x" * 13, "Spar\x1b[2Jky", "-dash", "emoji🙂", None, 42))
    check("nicknames: letters/numbers/space . ' - up to 12; anything else refused", nick_ok and nick_bad)
    (ts2.APP_DIR / "pokedex.txt").write_text("pikachu 2\n")
    col.reconcile()
    a = col.record_catch("pikachu", False, "Ash", "2026-11-25T10:00:00+00:00", level=7)
    (ts2.APP_DIR / "pokedex.txt").write_text("pikachu 3\n")
    col.reconcile()
    check("the trainer's level is recorded at catch time ('met at Lv 7')", col.get(a["id"]).get("met_level") == 7)
    col.set_nickname(a["id"], "Sparky")
    col.set_favourite(a["id"], True)
    rec = col.get(a["id"])
    check("a nickname and a favourite mark are kept", rec.get("nickname") == "Sparky" and rec.get("favourite") is True)
    try:
        col.choose(a["id"], "pikachu", False); refused = False
    except col.FavouriteError:
        refused = True
    check("a favourite can't be chosen for a trade", refused)
    fallback = col.choose(None, "pikachu", False)
    check("picking 'any' Pikachu never picks the favourite", fallback and fallback["id"] != a["id"])
    out = col.for_trade(rec)
    check("its nickname and met level travel in trades, the favourite mark doesn't",
          out.get("nickname") == "Sparky" and out.get("met_level") == 7 and "favourite" not in out)
    for r in col.of_kind("pikachu"):
        col.set_favourite(r["id"], True)
    try:
        col.choose(None, "pikachu", False); all_refused = False
    except col.FavouriteError:
        all_refused = True
    check("when they're all favourites, none can be traded", all_refused)
    col.set_nickname(a["id"], "")
    check("an empty nickname clears it", col.get(a["id"]).get("nickname") is None)
    hostile = col.clean_details({"name": "pikachu", "shiny": False, "nickname": "\x1b]0;pwn\x07", "met_level": True},
                                "pikachu", False)
    check("a hostile nickname or level from another trainer is dropped",
          hostile["nickname"] is None and hostile["met_level"] is None)
    check("catch numbers read naturally", [col.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101)] ==
          ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st"])

    # Search
    import search as srch
    names = []
    for f in sorted(store.GEN_DIR.glob("gen*_list.txt"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0)):
        names += f.read_text().split()
    entries = [{"name": n, "no": i + 1} for i, n in enumerate(names)]
    top = lambda q: [e["name"] for e in srch.rank(entries, q)[:3]]
    check("search: 'char' finds Charmander, Charmeleon, Charizard first", top("char") == ["charmander", "charmeleon", "charizard"])
    check("search: '150' and '#150' find Mewtwo", top("150")[:1] == ["mewtwo"] and top("#150")[:1] == ["mewtwo"])
    check("search: spaces, hyphens and capitals don't matter (Mr. Mime)",
          top("mr mime")[:1] == top("Mr-Mime")[:1] == top("mrmime")[:1] == ["mr-mime"])
    check("search: a word inside the name matches ('mime' -> Mime Jr. and Mr. Mime)", set(top("mime")[:2]) == {"mime-jr", "mr-mime"})
    check("search: nothing matches nonsense", top("zzzz") == [])
    check("search: the exact name always comes first ('mew' -> Mew before Mewtwo)", top("mew")[:2] == ["mew", "mewtwo"])

    # Settings
    import settings as st
    st.FILE = store.APP_DIR / "settings.conf"
    check("settings: defaults when there's no file", st.load() == st.DEFAULTS)
    st.save({"generations": [3, 1], "system_details": False, "animations": False, "update_check": True})
    got = st.load()
    check("settings: what's saved is what's loaded",
          got["generations"] == [1, 3] and got["system_details"] is False and got["animations"] is False
          and got["update_check"] is True)
    st.FILE.write_text("generations=0,9\nanimations=sometimes\nsystem_details=off\n")
    got = st.load()
    check("settings: invalid values are ignored (defaults used), valid ones kept",
          got["generations"] == st.ALL_GENERATIONS and got["animations"] is True and got["system_details"] is False)
    st.save({"generations": []})
    check("settings: you can never end up with no generations", st.load()["generations"] == st.ALL_GENERATIONS)
    import trade_ui
    played = []
    real = trade_ui.animate_send, trade_ui.animate_receive
    trade_ui.animate_send = lambda *a: played.append("send")
    trade_ui.animate_receive = lambda *a: played.append("receive")
    page = trade_ui.TradePage.__new__(trade_ui.TradePage)
    page.refresh_items = page.new_achievements = lambda: []
    flashed = []
    page.flash = flashed.append
    result = {"gave": {"name": "pikachu", "shiny": False}, "got": {"name": "staryu", "shiny": False}, "peer": "Misty"}
    try:
        for on in (True, False):
            played.clear()
            st.save({"animations": on})
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):   # it clears the screen
                page.finish_trade(result)
            check(f"trade animations {'on' if on else 'off'} in settings: "
                  f"{'played' if on else 'skipped'}, and the trade still completes",
                  played == (["send", "receive"] if on else []) and "Trade complete!" in flashed[-1])
    finally:
        trade_ui.animate_send, trade_ui.animate_receive = real
    st.FILE.unlink()

    # Catch history (catch_history.py)
    import catch_history as ch
    def ev(kind, name, when=None, shiny=False):
        p = {"name": name, "shiny": shiny}
        if when:
            p["caught_at"] = when
        return {"e": kind, "id": secrets_hex(), "pokemon": p}
    special = ({"mewtwo"}, {"mew"}, {"dragonite"})
    events = [ev("legacy", "rattata"), ev("legacy", "pidgey"),
              ev("caught", "pidgey", "2026-11-02T23:30:00+00:00"),
              ev("caught", "rattata", "2026-11-01T22:10:00+00:00"),            # earliest, though logged second
              ev("caught", "pidgey", "2026-11-02T23:45:00+00:00"),
              ev("caught", "dragonite", "2026-11-03T08:00:00+00:00"),
              ev("caught", "mewtwo", "2026-11-04T14:00:00+00:00"),
              ev("caught", "pidgey", "2026-11-05T01:00:00+00:00"),
              ev("received", "mew", "2026-11-06T12:00:00+00:00"),              # traded in: not your catch
              ev("caught", "zubat", "not a time")]                             # unreadable: skipped
    st_ = ch.stats(events, special)
    check("history: counts catches with times, and those from before 0.0.8",
          st_["total"] == 6 and st_["before"] == 2 and st_["days"] == 5)
    check("history: first and latest catch go by time, not log order",
          st_["first"][1]["name"] == "rattata" and st_["latest"][1]["name"] == "pidgey")
    check("history: rarest is Legendary over pseudo-legendary (a traded-in Mythical doesn't count)",
          st_["rarest"][1]["name"] == "mewtwo" and st_["rarest"][2] == "Legendary")
    st_ = ch.stats(events + [ev("caught", "caterpie", "2026-11-07T10:00:00+00:00", shiny=True)], special)
    check("history: a shiny beats a Legendary", st_["rarest"][1]["name"] == "caterpie")
    st_ = ch.stats(events + [ev("caught", "mew", "2026-11-08T10:00:00+00:00", shiny=True)], special)
    check("history: a shiny Mythical beats everything", st_["rarest"][2] == "Shiny and Mythical!")
    st_ = ch.stats(events, special)
    check("history: busiest day is the day with most catches", str(st_["busiest"][0]) == "2026-11-02" and st_["busiest"][1] == 2)
    check("history: catches sorted into morning/afternoon/evening/night",
          st_["parts"] == {"morning": 1, "afternoon": 1, "evening": 0, "night": 4})
    check("history: 'You're a night-time catcher'", st_["verdict"] == "You're a night-time catcher")
    few = ch.stats(events[:4], special)
    check("history: no verdict from too few catches", few["verdict"] is None and few["total"] == 2)
    tie = ch.stats([ev("caught", "pidgey", f"2026-11-0{d}T{h}:00:00+00:00") for d, h in
                    ((1, "08"), (2, "09"), (3, "14"), (4, "15"), (5, "18"), (6, "19"))], special)
    check("history: a tie says you catch at all hours", tie["verdict"] == "You catch at all hours")
    empty = ch.stats([ev("legacy", "rattata")], special)
    lines, _info = ch.screen(empty, 46)
    check("history: nothing recorded yet is explained, not an error",
          empty["total"] == 0 and any("No catch times recorded yet" in l for l in lines))
    import re as _re
    for w in (24, 30, 46):
        lines, _info = ch.screen(ch.stats(events, special), w)
        widest = max(len(_re.sub(r"\x1b\[[0-9;]*m", "", l)) for l in lines)
        check(f"history: the page fits a {w}-wide screen", widest <= w)

    # Moving to a new computer (transfer.py): the parts that guard against bad files
    import io as _io, tarfile as _tar
    import transfer as tr
    tr.SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}          # quicker, same code paths

    def sealed(members, pw="right-passphrase"):
        buf = _io.BytesIO()
        with _tar.open(fileobj=buf, mode="w:gz") as t:
            for name, data in members:
                info = _tar.TarInfo(name); info.size = len(data)
                t.addfile(info, _io.BytesIO(data))
        return tr.seal(buf.getvalue(), pw)

    def refused(fn):
        try:
            fn(); return False
        except tr.TransferError:
            return True

    good = {"pokedex.txt": b"pikachu 2\n", "trainer.json": b'{"name": "Ash"}'}
    payload = tr._bundle(good, "test")
    got, _m = tr._unbundle(tr.unseal(tr.seal(payload, "right-passphrase"), "right-passphrase"))
    check("export file: what's sealed comes back exactly", got == good)
    blob = tr.seal(payload, "right-passphrase")
    check("export file: a wrong passphrase is refused", refused(lambda: tr.unseal(blob, "wrong-passphrase")))
    flipped = bytearray(blob); flipped[len(blob) // 2] ^= 1
    check("export file: a single changed byte is refused", refused(lambda: tr.unseal(bytes(flipped), "right-passphrase")))
    check("export file: a cut-short copy is refused", refused(lambda: tr.unseal(blob[:-40], "right-passphrase")))
    evil = sealed([("../../.zshrc", b"rm -rf ~"), ("manifest.json", b"{}")])
    check("export file: a path outside poketerm's folder is refused, never written",
          refused(lambda: tr._unbundle(tr.unseal(evil, "right-passphrase"))))
    lnk = _io.BytesIO()
    with _tar.open(fileobj=lnk, mode="w:gz") as t:
        info = _tar.TarInfo("pokedex.txt"); info.type = _tar.SYMTYPE; info.linkname = "/etc/passwd"; t.addfile(info)
    check("export file: links inside it are refused",
          refused(lambda: tr._unbundle(tr.unseal(tr.seal(lnk.getvalue(), "pw-pw-pw-pw"), "pw-pw-pw-pw"))))
    import json as _json, hashlib as _h
    man = _json.dumps({"format": 1, "files": {"pokedex.txt": _h.sha256(b"mew 1\n").hexdigest()}}).encode()
    swapped = sealed([("manifest.json", man), ("pokedex.txt", b"mew 99\n")])
    check("export file: contents that don't match its list are refused",
          refused(lambda: tr._unbundle(tr.unseal(swapped, "right-passphrase"))))
    check("export file: a Pokedex with odd lines is refused",
          refused(lambda: tr._validate({"pokedex.txt": b"pikachu lots\n"})))
    e1 = {"seq": 1, "event": "commit", "trade_id": "t1", "prev": store.GENESIS}; e1["hash"] = store._entry_hash(e1)
    e2 = {"seq": 2, "event": "commit", "trade_id": "t2", "prev": e1["hash"]}; e2["hash"] = store._entry_hash(e2)
    journal = (_json.dumps(e1) + "\n" + _json.dumps(e2) + "\n").encode()
    check("export file: a sound trade history is accepted (and counted)", tr._check_journal(journal.decode()) == 2)
    check("export file: an edited trade history is refused",
          refused(lambda: tr._validate({"pokedex.txt": b"", "trade/trades.log": journal.replace(b'"t1"', b'"t9"')})))
    ident = store.load_identity()
    key, crt = store.KEY_FILE.read_bytes(), store.CERT_FILE.read_bytes()
    check("export file: a matching identity key and certificate are accepted",
          tr._check_identity(key, crt) == ident.fingerprint)
    store.KEY_FILE, store.CERT_FILE = store.KEY_FILE.with_name("other.key"), store.CERT_FILE.with_name("other.crt")
    store._create_identity()
    other_key = store.KEY_FILE.read_bytes()
    store.KEY_FILE, store.CERT_FILE = store.KEY_FILE.with_name("identity.key"), store.CERT_FILE.with_name("identity.crt")
    check("export file: someone else's key with your certificate is refused",
          refused(lambda: tr._check_identity(other_key, crt)))
    check("export file: half an identity (key, no certificate) is refused",
          refused(lambda: tr._validate({"pokedex.txt": b"", "trade/identity.key": key})))
    os.environ[tr.PASSPHRASE_ENV] = "short"
    check("export file: a passphrase under 8 characters isn't allowed", refused(lambda: tr._passphrase(confirm=True)))
    del os.environ[tr.PASSPHRASE_ENV]

    # Trading achievement rules
    import achievements as ach
    def t(gave, got, peer="a" * 64, day="2026-11-25", gs=False, rs=False):
        return {"gave": {"name": gave, "shiny": gs}, "got": {"name": got, "shiny": rs}, "peer": peer, "date": day}
    check("no trades: no trading achievements", ach.trade_achievements([]) == set())
    one = ach.trade_achievements([t("pikachu", "eevee")])
    check("first trade unlocks 'Nice doing business with you' (and nothing hidden)",
          one == {"Nice doing business with you"})
    four = [t("pikachu", "eevee", day=f"2026-11-{d:02d}") for d in range(1, 5)]
    check("4 trades isn't 5", "Regular at the Trade Centre" not in ach.trade_achievements(four))
    five_days = four + [t("pikachu", "eevee", day="2026-11-05")]
    got5 = ach.trade_achievements(five_days)
    check("5 trades unlocks 'Regular at the Trade Centre'", "Regular at the Trade Centre" in got5)
    check("5 trades on different days isn't 'Busy day'", "Busy day at the Trade Centre" not in got5)
    check("5 trades in one day unlocks 'Busy day at the Trade Centre'",
          "Busy day at the Trade Centre" in ach.trade_achievements([t("pikachu", "eevee")] * 5))
    check("25 and 100 trades unlock 'Swap Shop' and 'Trade Tycoon'",
          {"Swap Shop", "Trade Tycoon"} <= ach.trade_achievements([t("a", "b", day=str(i)) for i in range(100)])
          and "Trade Tycoon" not in ach.trade_achievements([t("a", "b", day=str(i)) for i in range(99)]))
    check("3 different trainers unlocks 'Making friends' (the same one 3 times doesn't)",
          "Making friends" in ach.trade_achievements([t("a", "b", peer=c * 64) for c in "abc"])
          and "Making friends" not in ach.trade_achievements([t("a", "b")] * 3))
    check("receiving a shiny unlocks 'Special Delivery'", "Special Delivery" in ach.trade_achievements([t("a", "b", rs=True)]))
    check("giving a shiny unlocks 'That's very generous!'",
          "That's very generous!" in ach.trade_achievements([t("a", "b", gs=True)]))
    check("receiving a Legendary unlocks 'A Legend Arrives'",
          "A Legend Arrives" in ach.trade_achievements([t("pikachu", "mewtwo")])
          and "A Legend Arrives" not in ach.trade_achievements([t("mewtwo", "pikachu")]))
    check("getting back a Pokemon you traded away unlocks 'Boomerang'",
          "Boomerang" in ach.trade_achievements([t("staryu", "pikachu"), t("squirtle", "staryu")])
          and "Boomerang" not in ach.trade_achievements([t("pikachu", "staryu"), t("staryu", "squirtle")]))
    check("there are exactly 10 trading achievements", len(ach.TRADE_ACHIEVEMENTS) == 10)

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
