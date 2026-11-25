"""Local state for trading: identity, Pokedex changes, the trade journal.

Everything a trade changes on disk goes through this module, under the same
lock poketerm takes when it records a catch, so trades and new terminals can
never overwrite each other.

How a trade is kept safe if anything fails part-way (crash, closed laptop,
dropped Wi-Fi):

  prepare  The Pokemon you give is taken out of your Pokedex and the signed
           trade terms are written to the journal. From here it is "in escrow":
           it can't be traded or removed again.
  commit   The Pokemon you receive is added. The trade is done.
  abort    The Pokemon you gave is put back. Nothing changed.

The protocol (trade_protocol.py) decides between commit and abort so that both
trainers always end up doing the same thing; if the connection drops at the
one moment that matters, the trade stays "pending" and is finished
automatically the next time the two trainers meet on the trade page.

The journal (trades.log) is append-only and hash-chained, so editing or
deleting a past entry is detected and trading refuses to continue.
"""
import base64
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Keys, certificates and signatures come from `cryptography`, which install.sh
# puts in poketerm's own Python. Without it (poketerm running on a system
# Python) the `openssl` command does the same job. Both make standard ECDSA
# P-256 keys and DER signatures, so trainers using either can trade together.
try:
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False

APP_DIR = Path.home() / ".local/share/poketerm"
POKEDEX_FILE = APP_DIR / "pokedex.txt"
POKEDEX_HASH = APP_DIR / "pokedex.txt.sha256"
POKEDEX_LOCK = APP_DIR / "pokedex.txt.lock"
GEN_DIR = APP_DIR / "gen_files"
TRADE_DIR = APP_DIR / "trade"
KEY_FILE = TRADE_DIR / "identity.key"
CERT_FILE = TRADE_DIR / "identity.crt"
JOURNAL = TRADE_DIR / "trades.log"
INFLIGHT = TRADE_DIR / "inflight.json"
KNOWN_TRAINERS = TRADE_DIR / "known_trainers.json"

DAILY_LIMIT = 5
LOCK_TIMEOUT = 30
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
LINE_RE = re.compile(r"^(?P<name>\S+)(?P<shiny> \(shiny\))? (?P<count>\d+)$")


class TradeError(Exception):
    """A problem the user can be told about in plain words."""


# ---------------------------------------------------------------------------
# Locking and small file helpers
# ---------------------------------------------------------------------------
class pokedex_lock:
    """The lock poketerm's zsh `zsystem flock` uses (an fcntl record lock on
    pokedex.txt.lock). Held for every read-check-write of trade state."""

    def __enter__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(POKEDEX_LOCK, "a+")
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.lockf(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() > deadline:
                    self._fh.close()
                    raise TradeError("Your Pokedex is busy (another terminal is using it). Try again.")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            fcntl.lockf(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()


def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_write(path, data, mode=0o600):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode())
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_trade_dir():
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TRADE_DIR, 0o700)


# ---------------------------------------------------------------------------
# Identity: a key and self-signed certificate made automatically on first use
# ---------------------------------------------------------------------------
def openssl_cmd():
    return shutil.which("openssl")


def check_requirements():
    """Return a plain-words reason trading can't work here, or None."""
    if not getattr(ssl, "HAS_TLSv1_3", False):
        return ("Trading needs Python with TLS 1.3 encryption "
                f"(this Python uses {ssl.OPENSSL_VERSION}). "
                "Install a newer Python 3, e.g. `brew install python`.")
    if not HAVE_CRYPTOGRAPHY and not openssl_cmd():
        return ("Trading needs poketerm's own Python (run `poketerm --update` to install it) "
                "or the `openssl` command.")
    return None


def cert_fingerprint(cert_pem):
    der = ssl.PEM_cert_to_DER_cert(cert_pem)
    return hashlib.sha256(der).hexdigest()


class Identity:
    def __init__(self, cert_pem):
        self.cert_pem = cert_pem
        self.fingerprint = cert_fingerprint(cert_pem)
        self.cert_path = str(CERT_FILE)
        self.key_path = str(KEY_FILE)


def load_identity():
    """This trainer's identity, created the first time trading is used."""
    _ensure_trade_dir()
    with open(TRADE_DIR / "identity.lock", "a+") as lk:
        fcntl.lockf(lk, fcntl.LOCK_EX)
        try:
            if not (KEY_FILE.exists() and CERT_FILE.exists()):
                _create_identity()
            os.chmod(KEY_FILE, 0o600)
            return Identity(CERT_FILE.read_text())
        finally:
            fcntl.lockf(lk, fcntl.LOCK_UN)


def _create_identity():
    if HAVE_CRYPTOGRAPHY:
        _create_identity_cryptography()
    else:
        _create_identity_openssl()


def _create_identity_cryptography():
    try:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "poketerm trainer")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=36500))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        _atomic_write(KEY_FILE, key_pem, 0o600)
        _atomic_write(CERT_FILE, cert.public_bytes(serialization.Encoding.PEM), 0o600)
    except (ValueError, OSError) as e:
        raise TradeError(f"Could not create your trading identity ({e}).")


def _create_identity_openssl():
    openssl = openssl_cmd()
    if not openssl:
        raise TradeError("Trading needs the `openssl` command.")
    work = tempfile.mkdtemp(dir=str(TRADE_DIR), prefix=".new-identity.")
    try:
        key, crt = os.path.join(work, "k"), os.path.join(work, "c")
        old = os.umask(0o077)
        try:
            subprocess.run(
                [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-nodes", "-keyout", key, "-out", crt, "-days", "36500",
                 "-subj", "/CN=poketerm trainer", "-addext", "basicConstraints=critical,CA:TRUE"],
                check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=30)
        finally:
            os.umask(old)
        cert_fingerprint(Path(crt).read_text())   # sanity check it parses
        os.replace(key, KEY_FILE)
        os.replace(crt, CERT_FILE)
        _fsync_dir(TRADE_DIR)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        raise TradeError(f"Could not create your trading identity ({e}).")
    finally:
        shutil.rmtree(work, ignore_errors=True)


_key_cache = {}


def sign(identity, data):
    """ECDSA-SHA256 signature of `data` with this trainer's key (base64, DER)."""
    if HAVE_CRYPTOGRAPHY:
        try:
            key = _key_cache.get(identity.key_path)
            if key is None:
                key = serialization.load_pem_private_key(Path(identity.key_path).read_bytes(), password=None)
                _key_cache[identity.key_path] = key
            return base64.b64encode(key.sign(data, ec.ECDSA(hashes.SHA256()))).decode()
        except (ValueError, TypeError, OSError) as e:
            raise TradeError(f"Could not sign the trade ({e}).")
    return _sign_openssl(identity, data)


def _sign_openssl(identity, data):
    try:
        out = subprocess.run([openssl_cmd(), "dgst", "-sha256", "-sign", identity.key_path],
                             input=data, capture_output=True, check=True, timeout=15).stdout
    except (subprocess.SubprocessError, OSError, TypeError) as e:
        raise TradeError(f"Could not sign the trade ({e}).")
    return base64.b64encode(out).decode()


def verify(cert_pem, data, signature_b64):
    """True if `signature_b64` is a valid signature of `data` by `cert_pem`'s key."""
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if not sig or len(sig) > 512:
        return False
    if HAVE_CRYPTOGRAPHY:
        try:
            public = x509.load_pem_x509_certificate(cert_pem.encode()).public_key()
            if not isinstance(public, ec.EllipticCurvePublicKey):
                return False
            public.verify(sig, data, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False
    return _verify_openssl(cert_pem, sig, data)


def _verify_openssl(cert_pem, sig, data):
    work = tempfile.mkdtemp(dir=str(TRADE_DIR), prefix=".verify.")
    try:
        cert_path, pub_path, sig_path = (os.path.join(work, n) for n in ("c", "p", "s"))
        Path(cert_path).write_text(cert_pem)
        Path(sig_path).write_bytes(sig)
        openssl = openssl_cmd()
        subprocess.run([openssl, "x509", "-in", cert_path, "-pubkey", "-noout", "-out", pub_path],
                       check=True, capture_output=True, timeout=15)
        r = subprocess.run([openssl, "dgst", "-sha256", "-verify", pub_path, "-signature", sig_path],
                           input=data, capture_output=True, timeout=15)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Pokemon names and the Pokedex file
# ---------------------------------------------------------------------------
_dex_order = None


def dex_order():
    """{name: national dex position} from the generation lists."""
    global _dex_order
    if _dex_order is None:
        order = {}
        for path in sorted(GEN_DIR.glob("gen*_list.txt"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0)):
            for name in path.read_text().split():
                order.setdefault(name, len(order) + 1)
        _dex_order = order
    return _dex_order


def is_valid_pokemon(name):
    return isinstance(name, str) and bool(NAME_RE.match(name)) and name in dex_order()


def _read_pokedex():
    try:
        return POKEDEX_FILE.read_text()
    except FileNotFoundError:
        return ""


def _parse(text):
    """{(name, shiny): count}; refuses lines it doesn't understand."""
    counts = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = LINE_RE.match(raw.strip())
        if not m:
            raise TradeError("Your Pokedex has a line poketerm doesn't recognise, so trading is paused. "
                             "Run `poketerm --fix-pokedex` and try again.")
        key = (m.group("name"), bool(m.group("shiny")))
        counts[key] = counts.get(key, 0) + int(m.group("count"))
    return counts


def _render(counts):
    """Pokedex text in the same order poketerm keeps it: National Dex order,
    normal before shiny, unknown names last."""
    order = dex_order()
    rows = [(order.get(n, 999999), int(s), f"{n}{' (shiny)' if s else ''} {c}")
            for (n, s), c in counts.items() if c > 0]
    rows.sort()
    return "".join(r[2] + "\n" for r in rows)


def _pokedex_untampered(text):
    """Same rule as poketerm: no checksum file yet is fine, otherwise it must match."""
    try:
        recorded = POKEDEX_HASH.read_text().split()[0]
    except (FileNotFoundError, IndexError):
        return True
    return hashlib.sha256(text.encode()).hexdigest() == recorded


def _stage_pokedex(text, trade_id, event):
    """Stage a Pokedex change that belongs to journal entry (trade_id, event).

    Order of a change: stage it here, append the journal entry, then
    _finish_inflight() swaps the file in and writes its checksum. If we stop
    anywhere in between, recover() applies the staged file only if its journal
    entry made it to disk, so the Pokedex and the journal always agree (and
    the checksum always matches, so poketerm never reports tampering).
    """
    new_hash = hashlib.sha256(text.encode()).hexdigest()
    _atomic_write(TRADE_DIR / "pokedex.staged", text, 0o444)
    _atomic_write(INFLIGHT, json.dumps({"trade_id": trade_id, "event": event, "new_hash": new_hash}))


def _finish_inflight():
    staged = TRADE_DIR / "pokedex.staged"
    try:
        info = json.loads(INFLIGHT.read_text())
    except FileNotFoundError:
        if staged.exists():
            staged.unlink()
        return
    except ValueError:
        info = {}
    logged = any(e["trade_id"] == info.get("trade_id") and e["event"] == info.get("event")
                 for e in _journal_entries())
    if logged and staged.exists() and hashlib.sha256(staged.read_bytes()).hexdigest() == info.get("new_hash"):
        os.replace(staged, POKEDEX_FILE)
        _fsync_dir(APP_DIR)
    if staged.exists():
        staged.unlink()             # never logged, or half-written: the change didn't happen
    current = hashlib.sha256(POKEDEX_FILE.read_bytes()).hexdigest() if POKEDEX_FILE.exists() else ""
    if logged and current == info.get("new_hash"):
        os.chmod(POKEDEX_FILE, 0o444)
        _atomic_write(POKEDEX_HASH, f"{current}  {POKEDEX_FILE}\n", 0o644)
    INFLIGHT.unlink()
    _fsync_dir(TRADE_DIR)


def recover():
    """Finish a Pokedex write a crash interrupted. Cheap when there's nothing to do."""
    if INFLIGHT.exists() or (TRADE_DIR / "pokedex.staged").exists():
        with pokedex_lock():
            _finish_inflight()


def tradeable_pokemon():
    """[{name, shiny, count}] the trainer can offer, in dex order, excluding
    anything already in escrow."""
    recover()
    with pokedex_lock():
        text = _read_pokedex()
        if not _pokedex_untampered(text):
            raise TradeError("Your Pokedex was changed outside poketerm, so trading is paused. "
                             "Run `poketerm --fix-pokedex` to repair it.")
        counts = _parse(text)
    order = dex_order()
    items = [{"name": n, "shiny": s, "count": c} for (n, s), c in counts.items()
             if c > 0 and n in order]
    items.sort(key=lambda i: (order[i["name"]], i["shiny"]))
    return items


# ---------------------------------------------------------------------------
# The trade journal (append-only, hash-chained)
# ---------------------------------------------------------------------------
GENESIS = "0" * 64


def _entry_hash(entry):
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _journal_entries():
    """All entries, verifying the chain. Raises TradeError if it was edited."""
    try:
        lines = JOURNAL.read_text().splitlines()
    except FileNotFoundError:
        return []
    entries, prev = [], GENESIS
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            if n == len(lines):      # a crash mid-append leaves a partial last line
                break
            raise TradeError("Your trade history (trades.log) is damaged, so trading is paused.")
        if entry.get("prev") != prev or entry.get("hash") != _entry_hash(entry):
            raise TradeError("Your trade history (trades.log) was changed outside poketerm, "
                             "so trading is paused.")
        entries.append(entry)
        prev = entry["hash"]
    return entries


def _journal_append(event, trade_id, **data):
    entries = _journal_entries()
    entry = {
        "seq": len(entries) + 1,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date": date.today().isoformat(),
        "event": event,
        "trade_id": trade_id,
        "prev": entries[-1]["hash"] if entries else GENESIS,
    }
    entry.update(data)
    entry["hash"] = _entry_hash(entry)
    _ensure_trade_dir()
    # Repair a torn last line from a crash before appending.
    if JOURNAL.exists():
        raw = JOURNAL.read_bytes()
        if raw and not raw.endswith(b"\n"):
            keep = raw[: raw.rfind(b"\n") + 1]
            _atomic_write(JOURNAL, keep)
    fd = os.open(str(JOURNAL), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return entry


def _trades(entries):
    """{trade_id: {"state": prepared|committed|aborted, "prepared": entry, ...}}"""
    trades = {}
    for e in entries:
        t = trades.setdefault(e["trade_id"], {})
        if e["event"] == "prepared":
            t["prepared"] = e
            t["state"] = "prepared"
        elif e["event"] in ("committed", "aborted"):
            t["state"] = e["event"]
            t[e["event"]] = e
    return trades


def trade_state(trade_id):
    """'prepared', 'committed', 'aborted' or None (never seen)."""
    with pokedex_lock():
        return _trades(_journal_entries()).get(trade_id, {}).get("state")


def _used_today(entries):
    today = date.today().isoformat()
    return sum(1 for t in _trades(entries).values()
               if t.get("state") in ("prepared", "committed") and t["prepared"]["date"] == today)


def trades_left_today():
    with pokedex_lock():
        return max(0, DAILY_LIMIT - _used_today(_journal_entries()))


def pending_trades():
    """Trades waiting to be finished with the other trainer: [prepared entry]."""
    with pokedex_lock():
        return [t["prepared"] for t in _trades(_journal_entries()).values() if t.get("state") == "prepared"]


def completed_trades():
    """Every completed trade, oldest first:
    [{"gave", "got": {"name", "shiny"}, "peer": fingerprint, "peer_name", "date"}].
    Read-only; [] if there's no trade history (or it can't be read)."""
    if not JOURNAL.exists():
        return []
    try:
        with pokedex_lock():
            trades = _trades(_journal_entries())
    except (TradeError, OSError):
        return []
    done = [t for t in trades.values() if t.get("state") == "committed" and "prepared" in t]
    done.sort(key=lambda t: t["committed"]["seq"])
    return [{"gave": t["prepared"]["give"], "got": t["prepared"]["receive"],
             "peer": t["prepared"]["peer"].get("fingerprint", ""),
             "peer_name": t["prepared"]["peer"].get("name", ""),
             "date": t["committed"]["date"]} for t in done]


def received_from(name, shiny):
    """Completed trades in which this trainer received `name` (normal or shiny),
    newest first: [{"peer": trainer name, "date": "YYYY-MM-DD"}]. Read-only;
    returns [] if there's no trade history (or it can't be read)."""
    if not JOURNAL.exists():
        return []
    try:
        with pokedex_lock():
            trades = _trades(_journal_entries())
    except (TradeError, OSError):
        return []
    found = []
    for t in trades.values():
        got = t.get("prepared", {}).get("receive") or {}
        if t.get("state") == "committed" and got.get("name") == name and bool(got.get("shiny")) == bool(shiny):
            found.append((t["committed"]["seq"], {"peer": t["prepared"]["peer"].get("name") or "Trainer",
                                                  "date": t["committed"]["date"]}))
    return [f for _, f in sorted(found, key=lambda x: x[0], reverse=True)]


def history(limit=20):
    with pokedex_lock():
        done = [t for t in _trades(_journal_entries()).values() if t.get("state") == "committed"]
    done.sort(key=lambda t: t["committed"]["seq"], reverse=True)
    return [t["prepared"] for t in done[:limit]]


# ---------------------------------------------------------------------------
# Trade steps. Each is idempotent and runs entirely under the Pokedex lock.
# ---------------------------------------------------------------------------
def _collection():
    """The individual-Pokemon records (collection.py). Their details are a bonus:
    if anything goes wrong with them, the trade itself carries on regardless."""
    try:
        import collection
        return collection
    except Exception:
        return None


def prepare(trade_id, give, receive, peer, terms, signatures, give_id=None, receive_details=None, me=""):
    """Take `give` out of the Pokedex (escrow) and record the signed terms.
    give/receive: {"name", "shiny"}; peer: {"name", "fingerprint"}."""
    recover()
    with pokedex_lock():
        entries = _journal_entries()
        known = _trades(entries).get(trade_id)
        if known:
            if known["state"] == "aborted":
                raise TradeError("This trade was already cancelled.")
            return                          # already prepared (or done): nothing to do
        if _used_today(entries) >= DAILY_LIMIT:
            raise TradeError(f"You've used all {DAILY_LIMIT} trades for today. Try again tomorrow.")
        text = _read_pokedex()
        if not _pokedex_untampered(text):
            raise TradeError("Your Pokedex was changed outside poketerm, so trading is paused. "
                             "Run `poketerm --fix-pokedex` to repair it.")
        counts = _parse(text)
        key = (give["name"], bool(give["shiny"]))
        if counts.get(key, 0) < 1:
            raise TradeError(f"You no longer have {display(give)} to trade.")
        counts[key] -= 1
        coll, sent, incoming = _collection(), None, None
        if coll:
            try:
                sent = coll.for_trade(coll.choose(give_id, give["name"], give["shiny"]))
                incoming = coll.clean_details(receive_details, receive["name"], receive["shiny"])
            except coll.FavouriteError as e:
                raise TradeError(str(e))      # favourites are protected: this one stops the trade
            except Exception:
                sent = incoming = None
        _stage_pokedex(_render(counts), trade_id, "prepared")
        _journal_append("prepared", trade_id, give=give, receive=receive, peer=peer,
                        terms=terms, signatures=signatures, sent_pokemon=sent,
                        receive_details=incoming, me=str(me)[:32])
        _finish_inflight()
        if coll and sent:
            try:
                coll.mark_sent(sent, trade_id, peer.get("name", ""))
            except Exception:
                pass


def commit(trade_id):
    """Add the received Pokemon. Safe to call more than once."""
    recover()
    with pokedex_lock():
        t = _trades(_journal_entries()).get(trade_id)
        if not t or t["state"] == "committed":
            return
        if t["state"] == "aborted":
            raise TradeError("This trade was cancelled.")
        receive = t["prepared"]["receive"]
        counts = _parse(_read_pokedex())
        key = (receive["name"], bool(receive["shiny"]))
        counts[key] = counts.get(key, 0) + 1
        _stage_pokedex(_render(counts), trade_id, "committed")
        _journal_append("committed", trade_id)
        _finish_inflight()
        coll = _collection()
        if coll:
            p = t["prepared"]
            try:
                coll.receive(p.get("receive_details"), receive["name"], receive["shiny"], trade_id,
                             p["peer"].get("name", ""), p.get("me", ""))
            except Exception:
                pass


def abort(trade_id, reason=""):
    """Cancel a trade, returning the escrowed Pokemon if it was taken. Safe to
    call more than once. Records the cancellation even for trades never
    prepared, so a late message can't prepare it afterwards."""
    recover()
    with pokedex_lock():
        t = _trades(_journal_entries()).get(trade_id)
        if t and t["state"] in ("committed", "aborted"):
            return
        if t and t["state"] == "prepared":
            give = t["prepared"]["give"]
            counts = _parse(_read_pokedex())
            key = (give["name"], bool(give["shiny"]))
            counts[key] = counts.get(key, 0) + 1
            _stage_pokedex(_render(counts), trade_id, "aborted")
            _journal_append("aborted", trade_id, reason=str(reason)[:200])
            _finish_inflight()
            coll = _collection()
            if coll and t["prepared"].get("sent_pokemon"):
                try:
                    coll.unsend(t["prepared"]["sent_pokemon"], trade_id)
                except Exception:
                    pass
        else:
            _journal_append("aborted", trade_id, reason=str(reason)[:200])


# ---------------------------------------------------------------------------
# Trainers met before (trust on first use)
# ---------------------------------------------------------------------------
def known_trainer(fingerprint):
    try:
        data = json.loads(KNOWN_TRAINERS.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return data.get(fingerprint)


def name_conflict(name, fingerprint):
    """True if a *different* trainer we've traded with used this name."""
    try:
        data = json.loads(KNOWN_TRAINERS.read_text())
    except (FileNotFoundError, ValueError):
        return False
    return any(v.get("name") == name and fp != fingerprint for fp, v in data.items())


def remember_trainer(fingerprint, name):
    _ensure_trade_dir()
    with open(TRADE_DIR / "known.lock", "a+") as lk:
        fcntl.lockf(lk, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(KNOWN_TRAINERS.read_text())
            except (FileNotFoundError, ValueError):
                data = {}
            entry = data.setdefault(fingerprint, {"first_seen": date.today().isoformat()})
            entry["name"] = name
            entry["last_traded"] = date.today().isoformat()
            _atomic_write(KNOWN_TRAINERS, json.dumps(data, indent=2))
        finally:
            fcntl.lockf(lk, fcntl.LOCK_UN)


def display(pokemon):
    name = pokemon["name"].replace("-", " ").title()
    return f"{name} (shiny)" if pokemon.get("shiny") else name


if __name__ == "__main__":
    # `python3 trade_store.py --recover` - used by poketerm after a crash.
    import sys
    if sys.argv[1:] == ["--recover"]:
        try:
            recover()
        except Exception:
            sys.exit(1)
