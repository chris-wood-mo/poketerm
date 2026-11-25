"""Local-network transport for trading.

Discovery: while the trade page is open, poketerm announces itself on the
local network once a second (UDP multicast and broadcast, never routed beyond
the local network) and listens for other trainers' announcements. Nothing is
announced or listened for at any other time.

Connections: a trainer connects to another's TCP port. The two first swap
their (public) certificates, then upgrade to TLS 1.3 with mutual
authentication, each side trusting exactly the certificate the other just
presented. Who is really on the other end is then confirmed by the 6-digit
pairing code (see trade_protocol.py); after that, every message is encrypted
and bound to both trainers' keys.

Everything here is replaceable: a remote transport only needs to provide a
Channel with the same methods.
"""
import errno
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

import trade_protocol as proto
import trade_store as store

DISCOVERY_GROUP = "239.255.42.99"
# POKETERM_DISCOVERY_PORT is for tests only: it gives a test run its own
# private discovery channel, so test trainers and real ones never see each other.
DISCOVERY_PORT = int(os.environ.get("POKETERM_DISCOVERY_PORT") or 47324)
BEACON_INTERVAL = 1.0
PEER_EXPIRY = 4.0
MAX_FRAME = 64 * 1024
MAX_HELLO = 8 * 1024
HANDSHAKE_TIMEOUT = 5
MAX_PENDING_HANDSHAKES = 16


class ChannelClosed(ConnectionError):
    pass


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------
def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ChannelClosed("connection closed")
        buf += chunk
    return bytes(buf)


def _send_frame(sock, obj, limit=MAX_FRAME):
    data = json.dumps(obj, separators=(",", ":")).encode()
    if len(data) > limit:
        raise ValueError("message too large")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv_frame(sock, limit=MAX_FRAME):
    (size,) = struct.unpack(">I", _recv_exact(sock, 4))
    if size > limit:
        raise ChannelClosed("message too large")
    try:
        return json.loads(_recv_exact(sock, size).decode())
    except (UnicodeDecodeError, ValueError):
        raise ChannelClosed("malformed message")


class Channel:
    """An authenticated, encrypted connection to one other trainer."""

    def __init__(self, tls_sock, peer_cert_pem):
        self.sock = tls_sock
        self.peer_cert_pem = peer_cert_pem
        self.peer_fingerprint = store.cert_fingerprint(peer_cert_pem)
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()
        self.closed = False

    def send(self, obj):
        if self.closed:
            raise ChannelClosed("connection closed")
        with self._send_lock:
            self.sock.settimeout(5)
            _send_frame(self.sock, obj)

    def recv(self, timeout=0.5):
        """The next message, or None if none arrived within `timeout`."""
        if self.closed:
            raise ChannelClosed("connection closed")
        with self._recv_lock:
            self.sock.settimeout(timeout)
            try:
                first = self.sock.recv(1)
            except (socket.timeout, ssl.SSLWantReadError):
                return None
            if not first:
                raise ChannelClosed("connection closed")
            self.sock.settimeout(10)
            size = struct.unpack(">I", first + _recv_exact(self.sock, 3))[0]
            if size > MAX_FRAME:
                raise ChannelClosed("message too large")
            try:
                return json.loads(_recv_exact(self.sock, size).decode())
            except (UnicodeDecodeError, ValueError):
                raise ChannelClosed("malformed message")

    def close(self, linger=1.0):
        """Hang up gracefully.

        Stop sending, then read (and discard) whatever the other side is still
        sending for up to `linger` seconds before closing. Closing a socket with
        unread data makes Linux reset the connection, and a reset throws away
        messages the other side has received but not yet read - such as our
        final "abort". Draining first means they always get it.
        """
        if self.closed:
            return
        self.closed = True
        try:
            with self._send_lock:
                self.sock.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + linger
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.05, deadline - time.monotonic()))
                if not self.sock.recv(65536):
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------
def _context(server, identity, peer_cert_pem):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(identity.cert_path, identity.key_path)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=peer_cert_pem)
    return ctx


def _hello(identity):
    return {"app": "poketerm-trade", "v": proto.PROTOCOL_VERSION, "cert": identity.cert_pem}


def _check_hello(obj):
    if not (isinstance(obj, dict) and obj.get("app") == "poketerm-trade" and isinstance(obj.get("cert"), str)):
        raise ChannelClosed("not a poketerm trainer")
    if obj.get("v") != proto.PROTOCOL_VERSION:
        raise ChannelClosed("the other trainer has a different poketerm version - both need to update")
    cert = obj["cert"]
    if len(cert) > 4096 or "-----BEGIN CERTIFICATE-----" not in cert:
        raise ChannelClosed("bad certificate")
    store.cert_fingerprint(cert)        # must parse
    return cert


def _verify_pinned(tls_sock, cert_pem):
    der = tls_sock.getpeercert(binary_form=True)
    if not der or ssl.DER_cert_to_PEM_cert(der).strip() != ssl.DER_cert_to_PEM_cert(
            ssl.PEM_cert_to_DER_cert(cert_pem)).strip():
        raise ChannelClosed("the other trainer's identity didn't match")


def connect(identity, host, port, expected_fingerprint=None, timeout=HANDSHAKE_TIMEOUT):
    """Open a Channel to the trainer at host:port."""
    raw = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        raw.settimeout(timeout)
        _send_frame(raw, _hello(identity), MAX_HELLO)
        cert = _check_hello(_recv_frame(raw, MAX_HELLO))
        if expected_fingerprint and store.cert_fingerprint(cert) != expected_fingerprint:
            raise ChannelClosed("that isn't the trainer you picked")
        tls = _context(False, identity, cert).wrap_socket(raw)
        _verify_pinned(tls, cert)
        return Channel(tls, cert)
    except (ssl.SSLError, ValueError) as e:
        raw.close()
        raise ChannelClosed(f"secure connection failed ({getattr(e, 'reason', None) or e})")
    except BaseException:
        raw.close()
        raise


class Listener:
    """Accepts trainers connecting to us while the trade page is open.
    on_channel(channel) is called on a background thread for each one."""

    def __init__(self, identity, on_channel, host="0.0.0.0"):
        self.identity = identity
        self.on_channel = on_channel
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._slots = threading.BoundedSemaphore(MAX_PENDING_HANDSHAKES)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                conn.close()            # too many half-open connections: drop
                continue
            threading.Thread(target=self._handshake, args=(conn,), daemon=True).start()

    def _handshake(self, conn):
        # The slot is held until on_channel returns (it reads the first
        # message and hands the trade to the UI, or settles a pending trade),
        # so idle or slow connections can't pile up.
        try:
            try:
                conn.settimeout(HANDSHAKE_TIMEOUT)
                cert = _check_hello(_recv_frame(conn, MAX_HELLO))
                if store.cert_fingerprint(cert) == self.identity.fingerprint:
                    raise ChannelClosed("that's you")
                _send_frame(conn, _hello(self.identity), MAX_HELLO)
                tls = _context(True, self.identity, cert).wrap_socket(conn, server_side=True)
                _verify_pinned(tls, cert)
                channel = Channel(tls, cert)
            except (OSError, ssl.SSLError, ValueError, ChannelClosed):
                try:
                    conn.close()
                except OSError:
                    pass
                return
            try:
                self.on_channel(channel)
            except Exception:
                channel.close()
        finally:
            self._slots.release()

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def local_address():
    """This computer's address on the local network (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # TEST-NET: only used to pick the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def network_interfaces():
    """[(name, address, broadcast)] for each IPv4 network this computer is on
    (not loopback). Read from `ifconfig` (macOS) or `ip` (Linux); falls back to
    the default route's address."""
    found = []
    try:
        if sys.platform == "darwin" or not shutil.which("ip"):
            ifconfig = shutil.which("ifconfig") or "/sbin/ifconfig"     # /sbin isn't always on PATH
            out = subprocess.run([ifconfig], capture_output=True, text=True, timeout=5).stdout
            name = None
            for line in out.splitlines():
                m = re.match(r"^(\S+?):? flags=", line)
                if m:
                    name = m.group(1).rstrip(":")
                    continue
                m = re.match(r"^\s+inet (\d+\.\d+\.\d+\.\d+) .*?broadcast (\d+\.\d+\.\d+\.\d+)", line)
                if m and name:
                    found.append((name, m.group(1), m.group(2)))
        else:
            out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.search(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/\d+ brd (\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    found.append(m.groups())
    except (OSError, subprocess.SubprocessError):
        pass
    found = [f for f in found if not f[1].startswith("127.")]
    if not found:
        addr = local_address()
        if not addr.startswith("127."):
            found = [("default", addr, "255.255.255.255")]
    return found


# Send errors that mean "the operating system won't let us talk to the local
# network" (on macOS 15+: the terminal app hasn't got Local Network access).
_BLOCKED = {errno.EHOSTUNREACH, errno.EPERM, errno.EACCES, errno.ENETUNREACH, errno.EADDRNOTAVAIL}


class Discovery:
    """Announces us and tracks other trainers with their trade page open.

    Announcements go out once a second on every network the computer is on
    (multicast and subnet broadcast). When we hear a trainer we answer them
    directly, so discovery works even if only one direction gets through.
    """

    def __init__(self, identity, name, port):
        self.identity = identity
        self.name = proto.clean_name(name)
        self.port = port
        self.peers = {}                  # fingerprint -> {name, host, port, seen, fp}
        self._replied = {}               # host -> when we last answered it directly
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.started = time.monotonic()
        self.interfaces = []
        self.sent_ok = 0
        self.send_errors = {}            # errno -> count (latest round)
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self._send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._recv_sock = self._make_receiver()
            self.listening = True
        except OSError:
            self._recv_sock = None
            self.listening = False
        threading.Thread(target=self._beacon_loop, daemon=True).start()
        if self._recv_sock:
            threading.Thread(target=self._listen_loop, daemon=True).start()

    @staticmethod
    def _make_receiver():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("", DISCOVERY_PORT))
        s.settimeout(0.5)
        return s

    def _join_groups(self):
        """Listen for multicast announcements on every network, not just the default one."""
        if not self._recv_sock:
            return
        for addr in ["0.0.0.0"] + [i[1] for i in self.interfaces]:
            mreq = struct.pack("4s4s", socket.inet_aton(DISCOVERY_GROUP), socket.inet_aton(addr))
            try:
                self._recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass                     # already joined, or that network can't multicast

    def _beacon(self, reply=False):
        return json.dumps({"app": "poketerm-trade", "v": proto.PROTOCOL_VERSION, "name": self.name,
                           "fp": self.identity.fingerprint, "port": self.port, "ts": int(time.time()),
                           "reply": reply}, separators=(",", ":")).encode()

    def _send(self, data, target, errors):
        try:
            self._send_sock.sendto(data, target)
            return True
        except OSError as e:
            errors[e.errno] = errors.get(e.errno, 0) + 1
            return False

    def _beacon_loop(self):
        last_scan = 0
        while not self._stop.is_set():
            if time.monotonic() - last_scan > 10:        # Wi-Fi changes, VPNs come and go
                self.interfaces = network_interfaces()
                self._join_groups()
                last_scan = time.monotonic()
            data, ok, errors = self._beacon(), 0, {}
            for _, addr, broadcast in self.interfaces:
                try:
                    self._send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr))
                except OSError:
                    pass
                ok += self._send(data, (DISCOVERY_GROUP, DISCOVERY_PORT), errors)
                ok += self._send(data, (broadcast, DISCOVERY_PORT), errors)
            ok += self._send(data, ("255.255.255.255", DISCOVERY_PORT), errors)
            self.sent_ok, self.send_errors = ok, errors
            self._stop.wait(BEACON_INTERVAL)

    def _listen_loop(self):
        while not self._stop.is_set():
            try:
                data, (host, _) = self._recv_sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                b = json.loads(data.decode())
                if not (isinstance(b, dict) and b.get("app") == "poketerm-trade"
                        and b.get("v") == proto.PROTOCOL_VERSION
                        and isinstance(b.get("fp"), str) and proto.HEX64.match(b["fp"])
                        and isinstance(b.get("port"), int) and 0 < b["port"] < 65536):
                    continue
            except (UnicodeDecodeError, ValueError):
                continue
            if b["fp"] == self.identity.fingerprint:
                continue
            with self._lock:
                if len(self.peers) >= 64 and b["fp"] not in self.peers:
                    continue
                self.peers[b["fp"]] = {"name": proto.clean_name(b.get("name")), "host": host,
                                       "port": b["port"], "seen": time.monotonic(), "fp": b["fp"]}
                # Answer directly (rate-limited), so they find us even if our
                # broadcasts don't reach them.
                due = b.get("reply") is not True and time.monotonic() - self._replied.get(host, 0) > 2
                if due:
                    self._replied[host] = time.monotonic()
            if due:
                self._send(self._beacon(reply=True), (host, DISCOVERY_PORT), {})

    def nearby(self):
        """Trainers seen in the last few seconds, sorted by name."""
        now = time.monotonic()
        with self._lock:
            for fp in [fp for fp, p in self.peers.items() if now - p["seen"] > PEER_EXPIRY]:
                del self.peers[fp]
            return sorted((dict(p) for p in self.peers.values()), key=lambda p: (p["name"].lower(), p["fp"]))

    def blocked(self):
        """True if the operating system refused every announcement we tried
        to send (e.g. macOS Local Network access is off for this terminal)."""
        return (time.monotonic() - self.started > 2 and self.sent_ok == 0
                and any(e in _BLOCKED for e in self.send_errors))

    def close(self):
        self._stop.set()
        for s in (self._send_sock, self._recv_sock):
            try:
                if s:
                    s.close()
            except OSError:
                pass
