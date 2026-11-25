"""The trade protocol: what two trainers say to each other to swap Pokemon.

This module knows nothing about networks. It talks to a Channel (anything with
send(dict), recv(timeout) -> dict|None, close(), peer_cert_pem and
peer_fingerprint), so the same protocol can run over the local network today
(trade_lan.py) and over a remote relay later.

Conversation ("a" invited "b"):

  a -> b  hello      purpose (trade or resolve), name, the Pokemon a offers
  b -> a  hello      name, commitment = sha256(b's secret nonce)
  a -> b  reveal     a's nonce
  b -> a  reveal     b's nonce (a checks it matches the commitment)
          Both now show the same 6-digit pairing code, made from both
          identities and both nonces. If someone is intercepting, the codes
          differ. b committed to its nonce before seeing a's, so an attacker
          can't keep trying nonces until the codes match.
  b -> a  answer     accept / decline (b's player sees the code and decides)
  either  offer      the Pokemon they will give (can change until both confirm)
  either  confirm    signature over the exact trade terms
          With both signatures, each side "prepares" (its Pokemon goes into
          escrow), then:
  either  ready      "I have prepared". Receiving the other side's ready
                     means the trade can't be cancelled any more: commit.
  either  done       "I have committed" (informational)
  either  abort      "I couldn't prepare / I cancelled". Only sent by a side
                     that has not sent ready, and only after it has recorded
                     the cancellation, so it can never prepare afterwards.

Why this never loses or duplicates a Pokemon: a side commits only after both
have prepared, and a side may cancel only before it has said ready. If the
connection drops after a side said ready but before it heard the other's, the
trade is left pending and settled with a "resolve" exchange the next time the
two trainers meet: if either side committed or prepared, both commit; if the
other side never prepared (or cancelled), both cancel.

Every message carries a random nonce and a timestamp: messages older than
CLOCK_WINDOW seconds, from the future, or repeated, are rejected.
"""
import hashlib
import json
import os
import queue
import re
import threading
import time

import trade_store as store

PROTOCOL_VERSION = 1
CLOCK_WINDOW = 120          # seconds a message stays valid
INVITE_TIMEOUT = 60         # seconds to answer an invite
IDLE_TIMEOUT = 300          # seconds a trade can sit idle before it is dropped
READY_TIMEOUT = 30          # seconds to wait for the other side's ready before leaving it pending
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NONCE = re.compile(r"^[0-9a-f]{32}$")


class ProtocolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def new_nonce():
    return os.urandom(16).hex()


def clean_name(value, limit=16):
    """A trainer name safe to print: no control/escape characters."""
    if not isinstance(value, str):
        return "Trainer"
    text = "".join(ch for ch in value if ch.isprintable() and ch not in "\x1b\u009b")
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    return text or "Trainer"


def pairing_code(fp_a, fp_b, nonce_a, nonce_b):
    digest = hashlib.sha256(canonical(["poketerm-pairing", fp_a, fp_b, nonce_a, nonce_b])).hexdigest()
    n = int(digest[:12], 16) % 1_000_000
    return f"{n // 1000:03d} {n % 1000:03d}"


def make_terms(session_id, a, b):
    """The exact deal both trainers sign. a/b: {fp, name, gives: {name, shiny}}."""
    body = {"app": "poketerm-trade", "v": PROTOCOL_VERSION, "session": session_id, "a": a, "b": b}
    body["trade_id"] = hashlib.sha256(canonical(body)).hexdigest()
    return body


def _pokemon(value):
    if not (isinstance(value, dict) and set(value) == {"name", "shiny"}
            and isinstance(value["shiny"], bool) and store.is_valid_pokemon(value["name"])):
        raise ProtocolError("unknown Pokemon")
    return {"name": value["name"], "shiny": value["shiny"]}


# Allowed fields for each message type: field -> validator returning the clean value.
_str = lambda limit: (lambda v: v if isinstance(v, str) and len(v) <= limit else _bad())
_hex64 = lambda v: v if isinstance(v, str) and HEX64.match(v) else _bad()
_nonce = lambda v: v if isinstance(v, str) and NONCE.match(v) else _bad()
_bool = lambda v: v if isinstance(v, bool) else _bad()
_int = lambda lo, hi: (lambda v: v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else _bad())
_opt_pokemon = lambda v: None if v is None else _pokemon(v)
_state = lambda v: v if v in ("committed", "prepared", "aborted", "unknown") else _bad()


def _bad():
    raise ProtocolError("malformed message")


def _details(v):
    """A traded Pokemon's story (dates, nature, trainers). Only its size and
    shape are checked here; collection.clean_details() checks every field
    before anything is stored or shown."""
    if v is None:
        return None
    if not isinstance(v, dict) or len(canonical(v)) > 4096:
        _bad()
    return v


SCHEMAS = {
    "hello":    {"name": _str(64), "purpose": lambda v: v if v in ("trade", "resolve") else _bad(),
                 "commit": _hex64, "nonce": _nonce, "trades_left": _int(0, store.DAILY_LIMIT),
                 "offer": _opt_pokemon},
    "reveal":   {"nonce": _nonce},
    "answer":   {"accept": _bool, "reason": _str(80)},
    "offer":    {"pokemon": _opt_pokemon, "details": _details},
    "confirm":  {"trade_id": _hex64, "signature": _str(512)},
    "ready":    {"trade_id": _hex64},
    "done":     {"trade_id": _hex64},
    "abort":    {"trade_id": lambda v: v if v == "" or HEX64.match(str(v)) else _bad(), "reason": _str(120)},
    "resolve":  {"trade_id": _hex64, "state": _state},
    "bye":      {"reason": _str(80)},
}
OPTIONAL = {"hello": {"commit", "nonce", "offer"}, "offer": {"details"}}


class Validator:
    """Checks every incoming message: known type, exact fields, fresh, unique."""

    def __init__(self, clock=time.time):
        self.seen = set()
        self.clock = clock

    def check(self, msg, *expected):
        if not isinstance(msg, dict):
            raise ProtocolError("malformed message")
        kind = msg.get("type")
        if kind not in SCHEMAS or (expected and kind not in expected):
            raise ProtocolError(f"unexpected message {kind!r}")
        if msg.get("v") != PROTOCOL_VERSION:
            raise ProtocolError("the other trainer has a different poketerm version - both need to update")
        nonce, ts = msg.get("nonce_id"), msg.get("ts")
        if not (isinstance(nonce, str) and NONCE.match(nonce)) or nonce in self.seen:
            raise ProtocolError("repeated or missing message id")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool) or abs(self.clock() - ts) > CLOCK_WINDOW:
            raise ProtocolError("message too old - check both computers' clocks are right")
        schema = SCHEMAS[kind]
        fields = set(msg) - {"type", "v", "nonce_id", "ts"}
        required = set(schema) - OPTIONAL.get(kind, set())
        if not required <= fields <= set(schema):
            raise ProtocolError("malformed message")
        clean = {k: schema[k](msg[k]) for k in fields}
        self.seen.add(nonce)
        clean["type"] = kind
        return clean


def message(kind, **fields):
    return dict(fields, type=kind, v=PROTOCOL_VERSION, nonce_id=new_nonce(), ts=time.time())


# ---------------------------------------------------------------------------
# A trade session
# ---------------------------------------------------------------------------
class TradeSession:
    """One trade between this trainer and one other.

    The UI calls accept()/decline()/set_offer()/confirm()/cancel() and reads
    `events` (a queue of (kind, data) tuples) plus snapshot(). All network work
    happens on a background thread; all state changes happen under self.lock.

    Phases: handshake -> invited (b deciding) / waiting (a waiting for answer)
            -> choosing -> committing -> done | failed | pending
    """

    def __init__(self, channel, identity, my_name, role, my_offer=None, first_hello=None,
                 validator=None, clock=time.time):
        assert role in ("a", "b")
        self.ch = channel
        self.me = identity
        self.my_name = clean_name(my_name)
        self.role = role
        self.lock = threading.RLock()
        self.events = queue.Queue()
        self.validator = validator or Validator(clock)
        self.first_hello = first_hello      # b: a's hello, already read by the listener
        self.phase = "handshake"
        self.peer_name = "Trainer"
        self.peer_fp = channel.peer_fingerprint
        self.peer_cert = channel.peer_cert_pem
        self.peer_trades_left = None
        self.code = None
        self.session_id = None
        self.my_offer = my_offer
        self.peer_offer = None
        self.my_details = None          # the exact Pokemon offered (collection record)
        self.peer_details = None        # the other trainer's, as they sent it (unchecked)
        self.my_sig = None
        self.peer_sig = None
        self.terms = None
        self.prepared = False
        self.ready_sent = False
        self.committed = False
        self.result = None           # {"gave", "got", "peer"} when done
        self.error = None
        self.last_activity = time.monotonic()
        self._thread = None

    # -- outgoing ----------------------------------------------------------
    def _send(self, kind, **fields):
        self.ch.send(message(kind, **fields))

    def _emit(self, kind, data=None):
        self.events.put((kind, data))

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._handshake()
            while True:
                with self.lock:
                    if self.phase in ("done", "failed", "pending"):
                        return
                    idle = time.monotonic() - self.last_activity
                    if self.ready_sent and idle > READY_TIMEOUT:
                        self._connection_lost()
                        return
                    limit = INVITE_TIMEOUT if self.phase in ("invited", "waiting") else IDLE_TIMEOUT
                    if idle > limit and not self.ready_sent:
                        self._fail_locally("The trade timed out." if self.phase != "invited"
                                           else "You didn't answer in time.", notify=True)
                        return
                msg = self.ch.recv(timeout=0.5)
                if msg is None:
                    continue
                with self.lock:
                    self.last_activity = time.monotonic()
                    self._handle(msg)
        except (ProtocolError, store.TradeError) as e:
            with self.lock:
                self._fail_locally(f"Trade stopped: {e}", notify=True)
        except (OSError, EOFError, ConnectionError, ValueError):
            with self.lock:
                self._connection_lost()
        finally:
            with self.lock:
                if self.phase not in ("done", "pending"):
                    if self.phase != "failed":
                        self._fail_locally("The other trainer left.", notify=False)
            try:
                self.ch.close()
            except OSError:
                pass

    def _recv_expect(self, *kinds, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.ch.recv(timeout=0.5)
            if msg is not None:
                clean = self.validator.check(msg, *kinds, "bye")
                if clean["type"] == "bye":
                    raise ProtocolError(clean["reason"] or "the other trainer left")
                return clean
        raise ProtocolError("the other trainer stopped responding")

    def _handshake(self):
        left = store.trades_left_today()
        if self.role == "a":
            self._send("hello", name=self.my_name, purpose="trade", trades_left=left, offer=self.my_offer)
            peer = self._recv_expect("hello")
            if "commit" not in peer:
                raise ProtocolError("malformed message")
            nonce_a = new_nonce()
            self._send("reveal", nonce=nonce_a)
            nonce_b = self._recv_expect("reveal")["nonce"]
            if hashlib.sha256(bytes.fromhex(nonce_b)).hexdigest() != peer["commit"]:
                raise ProtocolError("the other trainer's pairing data didn't check out")
            fp_a, fp_b = self.me.fingerprint, self.peer_fp
        else:
            peer = self.first_hello
            if not peer or peer.get("purpose") != "trade":
                raise ProtocolError("malformed message")
            nonce_b = new_nonce()
            commit = hashlib.sha256(bytes.fromhex(nonce_b)).hexdigest()
            self._send("hello", name=self.my_name, purpose="trade", commit=commit, trades_left=left)
            nonce_a = self._recv_expect("reveal")["nonce"]      # a reveals first...
            self._send("reveal", nonce=nonce_b)                 # ...then b, matching its commitment
            fp_a, fp_b = self.peer_fp, self.me.fingerprint
        with self.lock:
            self.peer_name = clean_name(peer["name"])
            self.peer_trades_left = peer["trades_left"]
            if self.role == "b":
                self.peer_offer = peer.get("offer")
            self.code = pairing_code(fp_a, fp_b, nonce_a, nonce_b)
            self.session_id = hashlib.sha256(canonical(["poketerm-session", fp_a, fp_b, nonce_a, nonce_b])).hexdigest()
            self.phase = "invited" if self.role == "b" else "waiting"
            self.last_activity = time.monotonic()
        self._emit("handshake")

    # -- UI actions --------------------------------------------------------
    def accept(self):
        with self.lock:
            if self.phase != "invited":
                return
            self._send("answer", accept=True, reason="")
            self.phase = "choosing"
            self.last_activity = time.monotonic()
        self._emit("changed")

    def decline(self, reason="declined"):
        with self.lock:
            if self.phase != "invited":
                return
            try:
                self._send("answer", accept=False, reason=reason[:80])
            except OSError:
                pass
            self.phase = "failed"
            self.error = "You declined the trade."
        self._emit("failed", self.error)

    def set_offer(self, pokemon, details=None):
        """Choose (or change) the Pokemon to give - `details` is the exact one
        (its collection record). Clears both confirmations."""
        with self.lock:
            if self.phase not in ("choosing", "waiting") or self.my_sig:
                return False        # after confirming, the only way out is cancel()
            self.my_offer = {"name": pokemon["name"], "shiny": bool(pokemon["shiny"])}
            self.my_details = details
            self.my_sig = self.peer_sig = None
            self.terms = None
            if self.phase == "choosing":
                self._send_offer()
            self.last_activity = time.monotonic()
        self._emit("changed")
        return True

    def _send_offer(self):
        fields = {"pokemon": self.my_offer}
        if self.my_details:
            fields["details"] = self.my_details
        self._send("offer", **fields)

    def confirm(self):
        """Sign the current terms. When both have signed, the trade runs."""
        with self.lock:
            if self.phase != "choosing" or not (self.my_offer and self.peer_offer) or self.my_sig:
                return
            terms = self._current_terms()
            self.terms = terms
            self.my_sig = store.sign(self.me, canonical(terms))
            self._send("confirm", trade_id=terms["trade_id"], signature=self.my_sig)
            self.last_activity = time.monotonic()
            self._maybe_prepare()
        self._emit("changed")

    def cancel(self):
        """Leave the trade. Allowed until this side has said ready."""
        with self.lock:
            if self.phase in ("done", "failed", "pending"):
                return True
            if self.ready_sent:
                return False
            trade_id = self.terms["trade_id"] if self.terms else ""
            if trade_id:
                store.abort(trade_id, "cancelled by you")
            try:
                self._send("abort", trade_id=trade_id, reason="The other trainer cancelled the trade.")
            except OSError:
                pass
            self.phase = "failed"
            self.error = "You cancelled the trade."
        self._emit("failed", self.error)
        return True

    def snapshot(self):
        with self.lock:
            return {k: getattr(self, k) for k in (
                "phase", "role", "peer_name", "peer_fp", "peer_trades_left", "code", "my_offer",
                "peer_offer", "my_sig", "peer_sig", "prepared", "ready_sent", "committed", "result", "error",
                "my_details", "peer_details")}

    # -- incoming ----------------------------------------------------------
    def _handle(self, raw):
        msg = self.validator.check(raw)
        kind = msg["type"]

        if kind == "bye":
            self._peer_cancelled(f"{self.peer_name} left the trade. Nothing was traded.")
            return

        if kind == "answer":
            if self.role != "a" or self.phase != "waiting":
                raise ProtocolError("unexpected answer")
            if msg["accept"]:
                self.phase = "choosing"
                if self.my_offer:
                    self._send_offer()
                self._emit("changed")
            else:
                self._fail_locally(f"{self.peer_name} declined the trade.", notify=False)
            return

        if kind == "offer":
            if self.phase != "choosing" or self.peer_sig:
                raise ProtocolError("the other trainer changed their offer after confirming")
            self.peer_offer = msg["pokemon"]
            self.peer_details = msg.get("details")
            self.my_sig = self.peer_sig = None
            self.terms = None
            self._emit("changed")
            return

        if kind == "confirm":
            if self.phase != "choosing" or not (self.my_offer and self.peer_offer):
                return                  # stale: offers changed while it was in flight
            terms = self._current_terms()
            if msg["trade_id"] != terms["trade_id"]:
                return                  # signed an older offer; they'll confirm again
            if not store.verify(self.peer_cert, canonical(terms), msg["signature"]):
                raise ProtocolError("the other trainer's signature was invalid")
            self.peer_sig = msg["signature"]
            self._emit("changed")
            self._maybe_prepare()
            return

        if kind == "ready":
            if not self.terms or msg["trade_id"] != self.terms["trade_id"] or not self.ready_sent:
                raise ProtocolError("unexpected ready")
            self._commit()
            return

        if kind == "done":
            return

        if kind == "abort":
            self._peer_cancelled(clean_name(msg["reason"], 120) if msg["reason"]
                                 else f"{self.peer_name} cancelled the trade.")
            return

        raise ProtocolError(f"unexpected message {kind!r}")

    # -- the commit sequence ------------------------------------------------
    def _current_terms(self):
        a_name, b_name = (self.my_name, self.peer_name) if self.role == "a" else (self.peer_name, self.my_name)
        a_fp, b_fp = (self.me.fingerprint, self.peer_fp) if self.role == "a" else (self.peer_fp, self.me.fingerprint)
        a_gives, b_gives = (self.my_offer, self.peer_offer) if self.role == "a" else (self.peer_offer, self.my_offer)
        return make_terms(self.session_id,
                          {"fp": a_fp, "name": a_name, "gives": a_gives},
                          {"fp": b_fp, "name": b_name, "gives": b_gives})

    def _maybe_prepare(self):
        if self.prepared or not (self.my_sig and self.peer_sig and self.terms):
            return
        self.phase = "committing"
        self._emit("changed")
        terms = self.terms
        sigs = {self.role: self.my_sig, ("b" if self.role == "a" else "a"): self.peer_sig}
        try:
            store.prepare(terms["trade_id"], give=self.my_offer, receive=self.peer_offer,
                          peer={"name": self.peer_name, "fingerprint": self.peer_fp},
                          terms=terms, signatures=sigs,
                          give_id=(self.my_details or {}).get("id"),
                          receive_details=self.peer_details, me=self.my_name)
        except store.TradeError as e:
            store.abort(terms["trade_id"], str(e))
            try:
                self._send("abort", trade_id=terms["trade_id"],
                           reason=f"{self.my_name} couldn't complete the trade.")
            except OSError:
                pass
            self._fail_locally(str(e), notify=False)
            return
        self.prepared = True
        self.ready_sent = True          # from here on we may not cancel
        try:
            self._send("ready", trade_id=terms["trade_id"])
        except OSError:
            # The other side may have hung up after sending an abort we
            # haven't read yet: keep reading. If nothing more arrives, the
            # trade is left pending and settled when we next meet.
            pass

    def _commit(self):
        store.commit(self.terms["trade_id"])
        store.remember_trainer(self.peer_fp, self.peer_name)
        self.committed = True
        self.phase = "done"
        self.result = {"gave": self.my_offer, "got": self.peer_offer, "peer": self.peer_name}
        try:
            self._send("done", trade_id=self.terms["trade_id"])
        except OSError:
            pass
        self._emit("done", self.result)

    def _peer_cancelled(self, text):
        """The other side sent abort/bye. It only does that before saying
        ready (having recorded the cancellation), so it can't have committed
        and never will: undo our escrow, even if we already said ready."""
        if self.committed or self.phase in ("done", "failed", "pending"):
            return
        if self.terms:
            store.abort(self.terms["trade_id"], text)
        self.phase = "failed"
        self.error = text + ("" if "Nothing" in text else " Nothing was traded.")
        self._emit("failed", self.error)

    def _fail_locally(self, text, notify):
        if self.phase in ("done", "failed", "pending"):
            return
        if self.ready_sent and not self.committed:
            self._connection_lost()
            return
        if self.terms and not self.committed:
            store.abort(self.terms["trade_id"], text)
        if notify:
            try:
                self._send("bye", reason=text[:80])
            except OSError:
                pass
        self.phase = "failed"
        self.error = text
        self._emit("failed", text)

    def _connection_lost(self):
        if self.phase in ("done", "failed", "pending"):
            return
        if self.ready_sent and not self.committed:
            self.phase = "pending"
            self.error = (f"The connection to {self.peer_name} dropped at the last step. Nothing is lost: "
                          f"the trade will finish automatically next time you both open the trade page.")
            self._emit("pending", self.error)
        else:
            if self.terms:
                store.abort(self.terms["trade_id"], "connection lost")
            self.phase = "failed"
            self.error = f"Lost the connection to {self.peer_name}. Nothing was traded."
            self._emit("failed", self.error)


# ---------------------------------------------------------------------------
# Settling a pending trade
# ---------------------------------------------------------------------------
def _decide(mine, theirs):
    """What a side with a prepared (in-doubt) trade does given the other's state."""
    if mine != "prepared":
        return None
    if theirs in ("committed", "prepared"):
        return "commit"
    return "abort"          # they never prepared, or cancelled: they can't commit


def resolve_session(channel, identity, my_name, first_hello=None, validator=None):
    """Settle pending trades with this trainer.

    Round 1: each side sends the state of every trade it has pending with the
    other, then "bye: sent". Each side then decides the trades both have
    pending, and answers any it was asked about but doesn't have pending
    (recording a never-seen trade as cancelled first, so it can never be
    prepared later). Round 2: answers, then "bye: finished".
    Returns [(trade_id, "committed"|"aborted")].
    """
    validator = validator or Validator()
    peer_fp = channel.peer_fingerprint
    mine = {p["trade_id"] for p in store.pending_trades() if p["peer"]["fingerprint"] == peer_fp}
    outcomes = []
    peer = {"name": clean_name(first_hello["name"]) if first_hello else "Trainer"}

    def state_of(tid):
        return store.trade_state(tid) or "unknown"

    def settle(tid, theirs):
        ours = state_of(tid)
        decision = _decide(ours, theirs)
        if decision == "commit":
            store.commit(tid)
            store.remember_trainer(peer_fp, peer["name"])
            outcomes.append((tid, "committed"))
        elif decision == "abort":
            store.abort(tid, f"settled: other trainer {theirs}")
            outcomes.append((tid, "aborted"))

    def read_until(marker, on_resolve, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = channel.recv(timeout=0.5)
            if raw is None:
                continue
            msg = validator.check(raw, "hello", "resolve", "bye")
            if msg["type"] == "hello":
                if msg["purpose"] != "resolve":
                    raise ProtocolError("unexpected message")
                peer["name"] = clean_name(msg["name"])
            elif msg["type"] == "resolve":
                on_resolve(msg["trade_id"], msg["state"])
            elif msg["reason"] == marker:
                return
            else:
                raise ProtocolError(msg["reason"] or "the other trainer left")
        raise ProtocolError("the other trainer stopped responding")

    channel.send(message("hello", name=clean_name(my_name), purpose="resolve",
                         trades_left=store.trades_left_today()))
    for tid in sorted(mine):
        channel.send(message("resolve", trade_id=tid, state=state_of(tid)))
    channel.send(message("bye", reason="sent"))

    asked = {}
    read_until("sent", lambda tid, st: asked.setdefault(tid, st))
    if len(asked) > 10:
        raise ProtocolError("too many trades to settle")
    for tid, theirs in asked.items():
        if tid in mine:
            settle(tid, theirs)
        else:
            if state_of(tid) == "unknown":
                store.abort(tid, "settled: never prepared here")
            channel.send(message("resolve", trade_id=tid, state=state_of(tid)))
    channel.send(message("bye", reason="finished"))

    answered = lambda tid, st: settle(tid, st) if tid in mine and tid not in asked else None
    read_until("finished", answered)
    return outcomes


def read_first_hello(channel, timeout=10):
    """For an incoming connection: read the other trainer's opening hello to
    learn what they want. Returns (hello, validator) - pass both on to
    TradeSession(role="b", ...) or resolve_session(...)."""
    validator = Validator()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = channel.recv(timeout=0.5)
        if raw is not None:
            return validator.check(raw, "hello"), validator
    raise ProtocolError("the other trainer stopped responding")
