"""Move your poketerm to another computer: `poketerm --export` / `--import`.

An export is one file holding everything that is *yours*:

    pokedex.txt          what you've caught
    trainer.json         name, level, XP, streak, achievements, favourites
    pokemon.log          each Pokemon's story (dates, natures, nicknames, trainers)
    settings.conf        your settings-page choices
    trade/identity.*     your trading identity (so other trainers still know you)
    trade/trades.log     your trade history and daily trade count
    trade/known_trainers.json

Because it holds your trading identity's private key, the file is always
encrypted with a passphrase you choose: scrypt turns the passphrase into keys,
AES-256-CTR encrypts, HMAC-SHA256 seals (checked before anything is decrypted
or read). A wrong passphrase, a damaged file or one that was tampered with is
refused without changing anything.

Importing checks every part (Pokedex lines, the trade history's hash chain,
that the identity key matches its certificate) before writing, keeps a backup
of this computer's progress first, and then swaps everything in under the same
locks poketerm uses, so a terminal opening at the same moment can't interleave.
"""
import base64
import fcntl
import getpass
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

import trade_store as store

MAGIC = b"POKETERM-EXPORT 1\n"
SUFFIX = ".poketerm"
MAX_FILE = 64 * 1024 * 1024           # far larger than any real export
MAX_MEMBER = 32 * 1024 * 1024
MIN_PASSPHRASE = 8
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
PASSPHRASE_ENV = "POKETERM_PASSPHRASE"  # for scripts (and tests); otherwise you're asked

# (path inside the export, path relative to the app dir, file mode)
FILES = [
    ("pokedex.txt", "pokedex.txt", 0o444),
    ("trainer.json", "trainer.json", 0o644),
    ("pokemon.log", "pokemon.log", 0o644),
    ("settings.conf", "settings.conf", 0o644),
    ("trade/identity.key", "trade/identity.key", 0o600),
    ("trade/identity.crt", "trade/identity.crt", 0o644),
    ("trade/trades.log", "trade/trades.log", 0o600),
    ("trade/known_trainers.json", "trade/known_trainers.json", 0o600),
]
ALLOWED = {f[0] for f in FILES} | {"manifest.json"}


class TransferError(Exception):
    """A problem explained in plain words; nothing was changed."""


def app_dir():
    return store.APP_DIR


# ---------------------------------------------------------------------------
# Encryption (one format, whether or not the `cryptography` package is here)
# ---------------------------------------------------------------------------
def _keys(passphrase, salt, n, r, p):
    material = hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p,
                              maxmem=128 * r * (n + p + 2) + 1024 * 1024, dklen=64)
    return material[:32], material[32:]


def _aes_ctr(key, iv, data):
    if store.HAVE_CRYPTOGRAPHY:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        c = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
        return c.update(data) + c.finalize()
    openssl = store.openssl_cmd()
    if not openssl:
        raise TransferError("Exporting needs poketerm's own Python (run `poketerm --update`) "
                            "or the `openssl` command.")
    try:
        return subprocess.run([openssl, "enc", "-aes-256-ctr", "-nosalt", "-K", key.hex(), "-iv", iv.hex()],
                              input=data, capture_output=True, check=True, timeout=60).stdout
    except (subprocess.SubprocessError, OSError) as e:
        raise TransferError(f"Couldn't encrypt ({e}).")


def seal(payload, passphrase):
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(16)
    header = dict(SCRYPT, kdf="scrypt", cipher="aes-256-ctr", mac="hmac-sha256",
                  salt=base64.b64encode(salt).decode(), iv=base64.b64encode(iv).decode())
    head = json.dumps(header, sort_keys=True).encode() + b"\n"
    enc_key, mac_key = _keys(passphrase, salt, SCRYPT["n"], SCRYPT["r"], SCRYPT["p"])
    body = MAGIC + head + _aes_ctr(enc_key, iv, payload)
    return body + hmac.new(mac_key, body, hashlib.sha256).digest()


def unseal(blob, passphrase):
    if not blob.startswith(MAGIC):
        raise TransferError("That isn't a poketerm export file.")
    nl = blob.find(b"\n", len(MAGIC))
    if nl < 0 or len(blob) < nl + 1 + 32:
        raise TransferError("That export file is damaged (it may not have copied completely).")
    try:
        h = json.loads(blob[len(MAGIC):nl])
        salt, iv = base64.b64decode(h["salt"], validate=True), base64.b64decode(h["iv"], validate=True)
        n, r, p = int(h["n"]), int(h["r"]), int(h["p"])
        if (h.get("kdf"), h.get("cipher"), h.get("mac")) != ("scrypt", "aes-256-ctr", "hmac-sha256"):
            raise ValueError
        # Bounded, so a crafted file can't make us use huge memory or time.
        if not (2 ** 14 <= n <= 2 ** 20 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4
                and len(salt) == 16 and len(iv) == 16):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise TransferError("That export file is damaged or from a newer poketerm.")
    body, tag = blob[:-32], blob[-32:]
    enc_key, mac_key = _keys(passphrase, salt, n, r, p)
    if not hmac.compare_digest(hmac.new(mac_key, body, hashlib.sha256).digest(), tag):
        raise TransferError("Wrong passphrase, or the file is damaged. Nothing was changed.")
    return _aes_ctr(enc_key, iv, body[nl + 1:])


# ---------------------------------------------------------------------------
# Reading this computer's progress, and checking an export's
# ---------------------------------------------------------------------------
class _trainer_lock:
    """The lock trainer.py / achievements.py hold while changing trainer.json."""

    def __enter__(self):
        app_dir().mkdir(parents=True, exist_ok=True)
        self._fh = open(app_dir() / "trainer.lock", "a+")
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    self._fh.close()
                    raise TransferError("Your trainer profile is busy (another terminal is using it). Try again.")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()


def _read_current():
    """{export path: bytes} of this computer's progress (call with the locks held)."""
    found = {}
    for name, rel, _ in FILES:
        path = app_dir() / rel
        if path.is_file():
            found[name] = path.read_bytes()
    return found


def _no_trade_in_progress():
    store.recover()
    if (store.TRADE_DIR / "inflight.json").exists():
        raise TransferError("A trade is still being saved. Open a new terminal, then try again.")
    try:
        pending = store.pending_trades()
    except store.TradeError as e:
        raise TransferError(str(e))
    if pending:
        raise TransferError("You have a trade that hasn't finished yet. Open the trade page (pokedex, then t) "
                            "with the other trainer nearby to finish it, then try again.")


def _check_journal(text):
    prev = store.GENESIS
    lines = text.splitlines()
    count = 0
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            if i == len(lines):
                break                  # a crash mid-append leaves a partial last line (as in trade_store)
            raise TransferError("The trade history in that file is damaged.")
        if entry.get("prev") != prev or entry.get("hash") != store._entry_hash(entry):
            raise TransferError("The trade history in that file was changed by hand, so it can't be imported.")
        prev = entry["hash"]
        count += entry.get("event") == "commit"
    return count


def _check_identity(key_pem, cert_pem):
    """The key must be the certificate's key: sign with one, verify with the other."""
    try:
        store.cert_fingerprint(cert_pem.decode())
    except (ValueError, UnicodeDecodeError):
        raise TransferError("The trading identity in that file is damaged.")
    store._ensure_trade_dir()
    work = tempfile.mkdtemp(dir=str(store.TRADE_DIR), prefix=".import-check.")
    try:
        key_path = Path(work) / "k"
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key_pem)
        ident = store.Identity(cert_pem.decode())
        ident.key_path = str(key_path)
        probe = secrets.token_bytes(32)
        try:
            ok = store.verify(cert_pem.decode(), probe, store.sign(ident, probe))
        except store.TradeError:
            ok = False
        store._key_cache.pop(str(key_path), None)
        if not ok:
            raise TransferError("The trading identity in that file doesn't match its key, so it can't be imported.")
        return ident.fingerprint
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _summary(files):
    """What a set of files holds, in a few plain words."""
    out = {"trainer": None, "level": None, "pokemon": 0, "species": 0, "shiny": 0, "trades": 0, "identity": False}
    try:
        counts = store._parse(files.get("pokedex.txt", b"").decode())
    except (store.TradeError, UnicodeDecodeError):
        counts = {}
    out["pokemon"] = sum(counts.values())
    out["species"] = len({n for (n, _s) in counts})
    out["shiny"] = sum(c for (_n, s), c in counts.items() if s)
    try:
        t = json.loads(files.get("trainer.json", b"{}"))
        out["trainer"], out["level"] = t.get("name") or None, t.get("level")
    except ValueError:
        pass
    try:
        out["trades"] = _check_journal(files.get("trade/trades.log", b"").decode())
    except (TransferError, UnicodeDecodeError):
        pass
    out["identity"] = "trade/identity.key" in files
    return out


def _describe(s):
    who = s["trainer"] or "an unnamed trainer"
    lvl = f", Lv {s['level']}" if s["level"] else ""
    shiny = f", {s['shiny']} shiny" if s["shiny"] else ""
    trades = f", {s['trades']} trade{'s' if s['trades'] != 1 else ''}" if s["trades"] else ""
    return f"{who}{lvl}: {s['pokemon']} Pokemon ({s['species']} species{shiny}){trades}"


def _validate(files):
    """Raise TransferError unless every part of an export is sound."""
    try:
        text = files["pokedex.txt"].decode()
    except KeyError:
        raise TransferError("That export has no Pokedex in it.")
    except UnicodeDecodeError:
        raise TransferError("The Pokedex in that file is damaged.")
    for line in text.splitlines():
        if line.strip() and not store.LINE_RE.match(line.strip()):
            raise TransferError("The Pokedex in that file has a line poketerm doesn't recognise.")
    if "trainer.json" in files:
        try:
            if not isinstance(json.loads(files["trainer.json"]), dict):
                raise ValueError
        except ValueError:
            raise TransferError("The trainer profile in that file is damaged.")
    if "trade/known_trainers.json" in files:
        try:
            if not isinstance(json.loads(files["trade/known_trainers.json"]), dict):
                raise ValueError
        except ValueError:
            raise TransferError("The list of trainers you've met in that file is damaged.")
    for name in ("pokemon.log", "settings.conf"):
        try:
            files.get(name, b"").decode()
        except UnicodeDecodeError:
            raise TransferError(f"The {name} in that file is damaged.")
    has_key, has_cert = "trade/identity.key" in files, "trade/identity.crt" in files
    if has_key != has_cert:
        raise TransferError("The trading identity in that file is incomplete.")
    if has_key:
        _check_identity(files["trade/identity.key"], files["trade/identity.crt"])
    try:
        _check_journal(files.get("trade/trades.log", b"").decode())
    except UnicodeDecodeError:
        raise TransferError("The trade history in that file is damaged.")


# ---------------------------------------------------------------------------
# Bundle format (a gzip'd tar, built and read in memory - never extracted to disk)
# ---------------------------------------------------------------------------
def _bundle(files, version):
    manifest = {
        "format": 1,
        "poketerm_version": version,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "from_computer": socket.gethostname().split(".")[0][:40],
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [("manifest.json", json.dumps(manifest, indent=2).encode())] + sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _unbundle(payload):
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            for m in tar:
                if m.name not in ALLOWED or not m.isfile() or m.size > MAX_MEMBER or m.name in files:
                    raise TransferError("That export file has something unexpected in it, so it wasn't imported.")
                files[m.name] = tar.extractfile(m).read()
    except (tarfile.TarError, OSError, EOFError):
        raise TransferError("That export file is damaged.")
    try:
        manifest = json.loads(files.pop("manifest.json"))
        sums = manifest["files"]
    except (KeyError, ValueError, TypeError):
        raise TransferError("That export file is damaged (no list of contents).")
    if manifest.get("format") != 1:
        raise TransferError("That export is from a newer poketerm. Update this one first: poketerm --update")
    if set(sums) != set(files) or any(hashlib.sha256(files[n]).hexdigest() != sums[n] for n in files):
        raise TransferError("That export file is damaged (its contents don't match).")
    return files, manifest


# ---------------------------------------------------------------------------
# Passphrases
# ---------------------------------------------------------------------------
def _passphrase(confirm):
    given = os.environ.get(PASSPHRASE_ENV)
    if given is not None:
        if confirm and len(given) < MIN_PASSPHRASE:
            raise TransferError(f"The passphrase needs at least {MIN_PASSPHRASE} characters.")
        return given
    if not sys.stdin.isatty():
        raise TransferError(f"Run this in a terminal so you can type a passphrase (or set {PASSPHRASE_ENV}).")
    try:
        if not confirm:
            return getpass.getpass("Passphrase: ")
        for _ in range(3):
            first = getpass.getpass(f"Choose a passphrase (at least {MIN_PASSPHRASE} characters): ")
            if len(first) < MIN_PASSPHRASE:
                print(f"  Too short - at least {MIN_PASSPHRASE} characters, please.")
                continue
            if getpass.getpass("Type it again: ") == first:
                return first
            print("  Those didn't match. Let's try again.")
    except (EOFError, KeyboardInterrupt):
        print()
    raise TransferError("No passphrase chosen, so nothing was exported.")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _default_name(summary):
    who = "".join(c for c in (summary["trainer"] or "trainer").lower() if c.isalnum())[:20] or "trainer"
    return f"poketerm-{who}-{datetime.now():%Y-%m-%d}{SUFFIX}"


def export(dest=None, version="unknown"):
    _no_trade_in_progress()
    with store.pokedex_lock(), _trainer_lock():
        files = _read_current()
        text = files.get("pokedex.txt", b"").decode(errors="replace")
        if not store._pokedex_untampered(text):
            raise TransferError("Your Pokedex was changed outside poketerm. Run `poketerm --fix-pokedex` first.")
    if "pokedex.txt" not in files:
        raise TransferError("There's no Pokedex to export yet - open a new terminal to catch your first Pokemon.")
    try:
        _check_journal(files.get("trade/trades.log", b"").decode())
    except UnicodeDecodeError:
        raise TransferError("Your trade history (trades.log) is damaged.")
    summary = _summary(files)
    path = Path(dest).expanduser() if dest else Path.cwd() / _default_name(summary)
    if path.is_dir():
        path = path / _default_name(summary)
    if path.exists():
        raise TransferError(f"{path} already exists. Choose another name: poketerm --export FILE")
    payload = _bundle(files, version)
    print(f"Exporting {_describe(summary)}.")
    print("The file is protected by a passphrase - you'll need it on the new computer.")
    blob = seal(payload, _passphrase(confirm=True))
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        raise TransferError(f"{path} already exists. Choose another name: poketerm --export FILE")
    except OSError as e:
        raise TransferError(f"Couldn't save to {path} ({e.strerror}).")
    print(f"\n✔ Saved {path} ({max(1, len(blob) // 1024)} KB)")
    print("\nOn your new computer: install poketerm, copy this file over, then run")
    print(f"    poketerm --import {path.name}")
    if summary["identity"]:
        print("\nIt holds your trading identity, so keep it private. Once it's imported,")
        print("trade from the new computer only (two computers sharing one identity would")
        print("each keep their own trade history).")
    return path


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def _backup_current():
    files = _read_current()
    if not files:
        return None
    dest = app_dir() / "backups" / f"{datetime.now():%Y%m%d-%H%M%S}-before-import"
    n = 1
    while dest.exists():
        n += 1
        dest = dest.with_name(f"{datetime.now():%Y%m%d-%H%M%S}-before-import-{n}")
    (dest / "trade").mkdir(parents=True)
    os.chmod(dest, 0o700)
    for name, data in files.items():
        store._atomic_write(dest / name, data, dict((f[0], f[2]) for f in FILES)[name] | 0o200)
    return dest


def _confirm(question):
    if not sys.stdin.isatty():
        raise TransferError("Run this in a terminal to confirm (or add --yes).")
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def import_(source, assume_yes=False):
    path = Path(source).expanduser()
    try:
        if path.stat().st_size > MAX_FILE:
            raise TransferError("That file is far too big to be a poketerm export.")
        blob = path.read_bytes()
    except FileNotFoundError:
        raise TransferError(f"There's no file called {path}.")
    except OSError as e:
        raise TransferError(f"Couldn't read {path} ({e.strerror}).")
    if not blob.startswith(MAGIC):
        raise TransferError("That isn't a poketerm export file.")
    files, manifest = _unbundle(unseal(blob, _passphrase(confirm=False)))
    _validate(files)
    incoming = _summary(files)
    when = manifest.get("exported_at", "")[:10]
    print(f"\nIn the file:      {_describe(incoming)}")
    print(f"                  exported {when} from {manifest.get('from_computer') or 'another computer'}")
    _no_trade_in_progress()
    here_summary = _summary(_read_current())
    if here_summary["pokemon"] or here_summary["trades"]:
        print(f"On this computer: {_describe(here_summary)}")
        print("\nImporting replaces this computer's progress with the file's.")
        print("(A backup of this computer's progress is kept in ~/.local/share/poketerm/backups.)")
    # Asked before taking the locks, so a terminal opening meanwhile isn't kept waiting.
    if not assume_yes and not _confirm("\nImport it?"):
        raise TransferError("Cancelled - nothing was changed.")
    _no_trade_in_progress()
    with store.pokedex_lock(), _trainer_lock():
        backup = _backup_current()       # taken under the locks: exactly what's being replaced
        _install(files)
    try:
        import collection
        collection.reconcile()
    except Exception:
        pass                          # the stories catch up on the next catch anyway
    print(f"\n✔ Imported. Welcome back, {incoming['trainer'] or 'trainer'}!")
    if backup:
        print(f"  This computer's previous progress is saved in {backup}")
    print("  Open the Pokedex with: pokedex")
    return incoming


def _install(files):
    """Swap the imported files in (locks held). Files the export doesn't have are
    removed, so nothing from this computer's old progress is left mixed in."""
    store._ensure_trade_dir()
    for name, rel, mode in FILES:
        target = app_dir() / rel
        if name in files:
            if target.exists():
                os.chmod(target, 0o600)           # pokedex.txt is read-only
            store._atomic_write(target, files[name], mode)
        elif target.exists():
            target.unlink()
    store._key_cache.clear()
    data = files["pokedex.txt"]
    store._atomic_write(store.POKEDEX_HASH, f"{hashlib.sha256(data).hexdigest()}  {store.POKEDEX_FILE}\n", 0o644)
    store._fsync_dir(store.TRADE_DIR)
    store._fsync_dir(app_dir())


def main(argv):
    version = "unknown"
    try:
        version = (app_dir() / "VERSION").read_text().strip() or version
    except OSError:
        pass
    try:
        if argv[:1] == ["export"] and len(argv) <= 2:
            export(argv[1] if len(argv) > 1 else None, version)
        elif argv[:1] == ["import"] and len(argv) >= 2:
            rest = argv[1:]
            yes = "--yes" in rest or "-y" in rest
            rest = [a for a in rest if a not in ("--yes", "-y")]
            if len(rest) != 1:
                raise TransferError("Usage: poketerm --import FILE [--yes]")
            import_(rest[0], yes)
        else:
            raise TransferError("Usage: poketerm --export [FILE]  |  poketerm --import FILE [--yes]")
    except (TransferError, store.TradeError) as e:
        print(f"✖ {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled - nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
