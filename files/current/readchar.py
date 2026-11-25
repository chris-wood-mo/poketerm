import os
import select
import sys
import termios
import tty


class _Key:
    LEFT = "\x1b[D"
    RIGHT = "\x1b[C"
    UP = "\x1b[A"
    DOWN = "\x1b[B"
    ENTER = "\r"


key = _Key()


def _read_byte(timeout=0.05):
    fd = sys.stdin.fileno()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return b""
    return os.read(fd, 1)


def _read_escape_sequence() -> bytes:
    """The rest of an escape sequence, and nothing more.

    Reads only as far as the sequence's final byte, so a key pressed straight
    after an arrow (or several arrows from a held-down key) isn't swallowed.
    ESC [ ... final (CSI) and ESC O x (SS3) are handled; a lone ESC returns
    nothing extra.
    """
    second = _read_byte()
    if second == b"[":
        seq = second
        while len(seq) < 16:
            b = _read_byte()
            if not b:
                break
            seq += b
            if 0x40 <= b[0] <= 0x7E:     # final byte of a CSI sequence
                break
        return seq
    if second == b"O":
        return second + _read_byte()
    return second


_in_cbreak = False


def _decode(first):
    if not first:
        return ""
    if first == b"\x1b":
        seq = first + _read_escape_sequence()
        mapping = {
            b"\x1b[A": key.UP, b"\x1bOA": key.UP,
            b"\x1b[B": key.DOWN, b"\x1bOB": key.DOWN,
            b"\x1b[C": key.RIGHT, b"\x1bOC": key.RIGHT,
            b"\x1b[D": key.LEFT, b"\x1bOD": key.LEFT,
        }
        return mapping.get(seq, seq.decode("utf-8", errors="replace"))
    if first in (b"\r", b"\n"):
        return key.ENTER
    return first.decode("utf-8", errors="replace")


class cbreak:
    """Keep the terminal in key-at-a-time, no-echo mode for a whole screen
    (e.g. one that redraws while waiting for keys), restoring it after."""

    def __enter__(self):
        global _in_cbreak
        self.fd = sys.stdin.fileno()
        self.original = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd, termios.TCSANOW)
        self.outer = _in_cbreak
        _in_cbreak = True
        return self

    def __exit__(self, *exc):
        global _in_cbreak
        _in_cbreak = self.outer
        if not self.outer:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.original)


def readkey() -> str:
    fd = sys.stdin.fileno()
    if _in_cbreak:
        return _decode(os.read(fd, 1))
    original = termios.tcgetattr(fd)
    try:
        # TCSANOW, not the default TCSAFLUSH: flushing would throw away keys
        # the user pressed while the screen was still drawing.
        tty.setcbreak(fd, termios.TCSANOW)
        return _decode(os.read(fd, 1))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def readkey_timeout(timeout):
    """Like readkey(), but returns None if no key is pressed within
    `timeout` seconds."""
    with cbreak():
        fd = sys.stdin.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        return _decode(os.read(fd, 1))


__all__ = ["key", "readkey", "readkey_timeout", "cbreak"]
