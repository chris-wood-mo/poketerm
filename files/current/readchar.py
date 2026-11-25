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


def _read_pending_bytes(count: int = 3) -> bytes:
    fd = sys.stdin.fileno()
    chunks = []
    total = 0

    while total < count:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            break
        chunk = os.read(fd, count - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)

    return b"".join(chunks)


def readkey() -> str:
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        # TCSANOW, not the default TCSAFLUSH: flushing would throw away keys
        # the user pressed while the screen was still drawing.
        tty.setcbreak(fd, termios.TCSANOW)
        first = os.read(fd, 1)
        if not first:
            return ""

        if first == b"\x1b":
            extra = _read_pending_bytes(3)
            seq = first + extra
            mapping = {
                b"\x1b[A": key.UP,
                b"\x1b[B": key.DOWN,
                b"\x1b[C": key.RIGHT,
                b"\x1b[D": key.LEFT,
            }
            return mapping.get(seq, seq.decode("utf-8", errors="replace"))

        if first in (b"\r", b"\n"):
            return key.ENTER

        return first.decode("utf-8", errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


__all__ = ["key", "readkey"]
