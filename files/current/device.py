"""The Pokedex device: draws every Pokedex screen inside a handheld Pokedex.

    left half                                   right half
    ┌──────────────────────────┐
    │ (lens)  ● ● ●            │╲___
    │  ┌────────────────────┐  │    ┌──────────────────────┐
    │  │                    │  │    │  green info display  │
    │  │   page content     │  │    └──────────────────────┘
    │  │                    │  │     [key][key][key][key]
    │  └────────────────────┘  │     [key][key][key][key]
    │  ●  ▬ ▬    ✚             │      ▬  ▬      ●
    │     [status]             │     [slot]    [slot]
    └──────────────────────────┘

A page calls show() with the lines for the screen (they may contain colours)
plus what the right half should show. Everything is drawn into a grid of
cells first and written in one go, so frames never flicker or tear.

The device needs a big enough window. When it doesn't fit, it drops the right
half (keys and info move under the screen), and in a small window it draws a
plain framed page, so poketerm works in any terminal size.

Colours use the 256-colour palette, which every terminal supports (including
macOS Terminal, which can't show 24-bit colour chrome reliably).
"""
import re
import shutil
import sys
import unicodedata

# -- palette (xterm 256 colours) ----------------------------------------------
CASE = 167        # body red
CASE_DARK = 124   # edges, hinge, shading
CASE_LINE = 88    # darkest red: seams
BEZEL = 252       # light grey screen surround
BEZEL_DARK = 245
SCREEN = 236      # the screen itself
SCREEN_FG = 255
GREEN = 107       # info display
GREEN_DARK = 22   # text on the info display
GREEN_BTN = 71
BLUE = 110        # buttons, lens, d-pad
BLUE_DARK = 25
YELLOW = 222
LIGHT_RED = 160
LIGHT_YELLOW = 220
LIGHT_GREEN = 76
LIGHT_OFF = 95
SLOT = 238        # small dark displays
SLOT_FG = 151

RIGHT_WIDTH = 38
HINGE = 3
MAX_KEYS = 12     # blue buttons on the right half: up to four rows of three
SIDE = 3          # casing margin left/right of the bezel
FRAME = 3         # bezel (2) + the screen's own inner margin (1), each side
BEZEL_ROWS = 4    # bezel rows above + below the screen: the ⊕ row, the screen's edge (x2), the speaker row
SCREEN_EDGE = 240 # the thin dark edge between the black screen and the white bezel
TOP_ROWS = 6      # casing above the screen bezel
BOTTOM_ROWS = 6   # casing below the screen bezel (controls)

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def char_width(ch):
    if unicodedata.combining(ch) or ch in "​‍️︎":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def fit_words(text, width):
    """`text` cut to `width` at a word boundary, never mid-word."""
    if len(text) <= width:
        return text
    cut = text[:width].rsplit(" ", 1)[0] if " " in text[:width] else text[:width]
    return cut.rstrip(" ·-/")


def visible_width(text):
    return sum(char_width(c) for c in CSI_RE.sub("", text))


def sgr(fg=None, bg=None, bold=False):
    codes = []
    if bold:
        codes.append("1")
    if fg is not None:
        codes.append(f"38;5;{fg}")
    if bg is not None:
        codes.append(f"48;5;{bg}")
    return f"\x1b[{';'.join(codes)}m" if codes else ""


class Canvas:
    """A grid of cells; each cell is (text, style). Wide characters take two
    cells: the second holds None and is skipped when writing."""

    def __init__(self, width, height, bg=None):
        self.w, self.h = width, height
        blank = sgr(bg=bg) if bg is not None else ""
        self.cells = [[(" ", blank) for _ in range(width)] for _ in range(height)]

    def put(self, x, y, text, fg=None, bg=None, bold=False):
        style = sgr(fg, bg, bold)
        for ch in text:
            w = char_width(ch)
            if w == 0:
                continue
            if 0 <= y < self.h and 0 <= x and x + w <= self.w:
                self.cells[y][x] = (ch, style)
                if w == 2:
                    self.cells[y][x + 1] = (None, style)
            x += w
        return x

    def fill(self, x, y, w, h, bg, ch=" ", fg=None):
        for yy in range(y, y + h):
            self.put(x, yy, ch * max(0, w), fg=fg, bg=bg)

    def put_ansi(self, x, y, line, width, base):
        """Draw a line that carries its own colour codes, clipped to `width`
        cells. A reset (ESC[0m) returns to `base` (the screen's own colours)."""
        style, col, i = base, 0, 0
        while i < len(line) and col < width:
            m = SGR_RE.match(line, i)
            if m:
                params = m.group(1)
                if params in ("", "0"):
                    style = base
                elif params.startswith("0;"):
                    style = base + f"\x1b[{params[2:]}m"
                else:
                    style += m.group(0)
                i = m.end()
                continue
            m = CSI_RE.match(line, i)
            if m:                                   # other control codes: ignore
                i = m.end()
                continue
            ch = line[i]
            i += 1
            w = char_width(ch)
            if w == 0 or ch in "\r\n":
                continue
            if col + w > width:
                break
            if 0 <= y < self.h and x + col + w <= self.w:
                self.cells[y][x + col] = (ch, style)
                if w == 2:
                    self.cells[y][x + col + 1] = (None, style)
            col += w

    def render(self):
        out = []
        for row in self.cells:
            parts, last = [], None
            for text, style in row:
                if text is None:
                    continue
                if style != last:
                    parts.append("\x1b[0m" + style)
                    last = style
                parts.append(text)
            out.append("".join(parts) + "\x1b[0m")
        return out


# -- layout -------------------------------------------------------------------
def terminal_size():
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


SLIM_CHROME = 8   # slim device rows besides the screen and its key buttons: top strip (2), bezel (4), gap, bottom edge


def _slim_key_rows(keys, width):
    """Rows the slim device's key buttons take, with a gap between rows like the full device."""
    n = max(1, len(_key_button_rows(list(keys), width)))
    return 2 * n - 1


def layout(content_width, content_height, right_rows=0, keys=()):
    """'full', 'half', 'slim' or 'plain' for a screen of this size in this window."""
    cols, rows = terminal_size()
    left = content_width + 2 * (SIDE + FRAME)
    height = content_height + TOP_ROWS + BEZEL_ROWS + BOTTOM_ROWS
    if left + HINGE + RIGHT_WIDTH <= cols and max(height, right_rows) <= rows - 1:
        return "full"
    slim_w = content_width + 2 * (1 + FRAME)
    if slim_w <= cols and content_height + SLIM_CHROME + _slim_key_rows(keys, slim_w - 4) <= rows - 1:
        return "slim"
    return "plain"


def screen_rows(content_width, wanted, extra_plain=0):
    """How many content rows a page can use (at most `wanted`), so a page can
    shrink its list to fit the window."""
    cols, rows = terminal_size()
    chrome = TOP_ROWS + BEZEL_ROWS + BOTTOM_ROWS
    for mode_rows in (rows - 1 - chrome, rows - 1 - SLIM_CHROME - 3):
        n = min(wanted, mode_rows)
        if n >= 6 and layout(content_width, n, keys=[("x", "x")] * 7) != "plain":
            return n
    return max(3, min(wanted, rows - 1 - PLAIN_EXTRA - extra_plain))


def fit_rows(content_width, info=(), keys=()):
    """The most screen rows that will be visible right now without anything
    scrolling off: on the device if it fits, otherwise the compact layout.
    Pages with tall content (big sprites) use this to decide what to show."""
    cols, rows = terminal_size()
    chrome = TOP_ROWS + BEZEL_ROWS + BOTTOM_ROWS
    left = content_width + 2 * (SIDE + FRAME)
    best = 0
    if left + HINGE + RIGHT_WIDTH <= cols and right_height(list(info), list(keys)) <= rows - 1:
        best = rows - 1 - chrome                                  # full device
    slim_w = content_width + 2 * (1 + FRAME)
    if slim_w <= cols:                                            # slim device
        best = max(best, rows - 1 - SLIM_CHROME - _slim_key_rows(keys, slim_w - 4))
    width = min(content_width, max(10, cols - 4))
    plain = rows - 1 - 2 - 2 - len(_wrap_keys(list(keys), width))  # compact: borders, rule, status, keys
    return max(best, plain, 3)


# -- drawing ------------------------------------------------------------------
_last_size = None


MIN_SLIM_WIDTH = 24      # narrowest screen the slim device will draw


def available_width():
    """The widest screen that fits this window (in the slim device, the
    narrowest form of the Pokedex). Pages with wide content - sprites, tables -
    use this to decide what to show."""
    cols, _ = terminal_size()
    return max(MIN_SLIM_WIDTH, cols - 2 * (1 + FRAME))


def wrap_lines(lines, width):
    """Wrap lines that are wider than `width` at word boundaries, keeping
    their colours (each continuation line restarts with the colours in effect)."""
    out = []
    for line in lines:
        if visible_width(line) <= width:
            out.append(line)
            continue
        plain = CSI_RE.sub("", line)
        if plain.strip() and set(plain.strip()) <= set("─━═-"):
            # a divider: trim it to the screen
            m = re.match(r"^((?:\x1b\[[0-9;]*m)*)", line)
            out.append(f"{m.group(1)}{plain.strip()[0] * width}\x1b[0m")
            continue
        if len(plain.strip()) <= width and plain != plain.lstrip(" "):
            # centred (or padded) text that only overflows because of its
            # padding: centre it again in the narrower screen
            body = re.sub(r"^((?:\x1b\[[0-9;]*m)*) +", r"\1", line)
            body = re.sub(r" +((?:\x1b\[[0-9;]*m)*)$", r"\1", body.rstrip())
            pad = max(0, (width - visible_width(body)) // 2)
            out.append(" " * pad + body)
            continue
        line = line.rstrip()
        if visible_width(line) <= width:
            out.append(line)
            continue
        active, current, col, last_space = "", "", 0, None
        i = 0
        while i < len(line):
            m = SGR_RE.match(line, i) or CSI_RE.match(line, i)
            if m:
                code = m.group(0)
                active = "" if code in ("\x1b[0m", "\x1b[m") else active + code
                current += code
                i = m.end()
                continue
            ch = line[i]
            w = char_width(ch)
            if col + w > width:
                if last_space is not None and last_space > 0:
                    head, tail = current[:last_space], current[last_space + 1:]
                    out.append(head + "\x1b[0m")
                    current = active_at_break + tail
                    col = visible_width(tail)
                else:
                    out.append(current + "\x1b[0m")
                    current, col = active, 0
                last_space = None
            if ch == " ":
                last_space, active_at_break = len(current), active
            current += ch
            col += w
            i += 1
        if CSI_RE.sub("", current).strip():
            out.append(current)
    return out


def show(lines, width=None, info=None, keys=(), slots=("", ""), lights=(True, False, False),
         status="", title="", wrap=True, info_on_screen=False):
    """Draw one frame of the Pokedex.

    lines   - the screen's content (may contain colours)
    width   - screen width in cells (default: widest line)
    info    - lines for the green display on the right
    keys    - [(key, label)] shown on the blue buttons
    slots   - two short texts for the small dark displays
    lights  - which of the red / yellow / green lights are on
    status  - short text for the green slot under the screen
    wrap    - in a window narrower than the page, wrap long lines (True) or
              clip them (False, for pictures such as animation frames)
    info_on_screen - in the slim and compact layouts (no green display), add
              `info` under the page's own lines. Only for pages where it says
              something the screen doesn't (e.g. a Pokemon's stats).
    """
    global _last_size
    info = info or []
    # A page's stated width is fixed: wider lines are clipped, never allowed to
    # stretch the device (e.g. a Poke Ball flying off the edge of the screen).
    width = width or max([visible_width(l) for l in lines] + [20])
    height = len(lines)
    mode = layout(width, height, right_height(info, keys), keys)
    cols, rows = terminal_size()
    if mode == "plain" and cols >= MIN_SLIM_WIDTH + 2 * (1 + FRAME) and rows >= SLIM_CHROME + 6:
        # Too narrow or short for the page as designed: keep the Pokedex look
        # with the slim device, narrowing its screen (and wrapping text) to fit.
        width = min(width, available_width())
        mode = "slim"
    if wrap:
        lines = wrap_lines(lines, width)      # text wider than the screen wraps, never gets cut off
    size = terminal_size()
    out = []
    if size != _last_size:
        out.append("\x1b[2J")            # window changed: clear what was there
        _last_size = size
    small_info = info if info_on_screen else []
    if mode == "plain":
        frame = _plain(lines, width, small_info, keys, slots, status, title)
    elif mode == "slim":
        frame = _slim(lines, width, small_info, keys, slots, lights, status)
    else:
        frame = _device(lines, width, info, keys, slots, lights, status, mode)
    # ESC[K after each row: clear anything left of a wider earlier frame.
    sys.stdout.write("".join(out) + "\x1b[H" + "\x1b[K\n".join(frame) + "\x1b[K\n\x1b[J")
    sys.stdout.flush()


def _key_text(keys):
    return "  ".join(f"\x1b[1m{k}\x1b[22m {label}" for k, label in keys)


PLAIN_EXTRA = 6   # rows the compact layout adds: 2 borders, a rule, a status line, 2 key lines


def _wrap_keys(keys, width):
    """Key hints over as many lines as needed to fit `width`."""
    lines, line = [], ""
    for k, label in keys:
        part = f"\x1b[1m{k}\x1b[22m {label}"
        plain_len = len(k) + 1 + len(label)
        if line and visible_width(line) + 2 + plain_len > width:
            lines.append(line)
            line = part
        else:
            line = f"{line}  {part}" if line else part
    if line:
        lines.append(line)
    return lines


def _plain(lines, width, info, keys, slots, status, title):
    """A compact framed screen for small windows: the content, one status
    line, the keys, and the info block too if there's room for it."""
    cols, rows = terminal_size()
    width = min(width, max(10, cols - 4))
    seen, parts = set(), []
    for x in list(slots) + [status]:                  # no repeated words
        if x and x.lower() not in seen:
            seen.add(x.lower())
            parts.append(x)
    status_line = "  ".join(parts)
    key_lines = _wrap_keys(keys, width)
    extra = [l for l in info if CSI_RE.sub("", l).strip()]
    footer = ["\x1b[38;5;245m" + "─" * width + "\x1b[0m", status_line] + key_lines
    room = max(1, rows - 1 - 2 - len(footer))       # never taller than the window
    body = list(lines)
    if extra and len(body) + 1 + len(extra) <= room:
        body += [""] + extra                        # the info block only when there's room
    if len(body) > room:
        body = body[: room - 1] + ["\x1b[38;5;245m(make the window taller to see more)\x1b[0m"]
    body += footer
    c = Canvas(width + 4, len(body) + 2)
    c.put(0, 0, "┌" + "─" * (width + 2) + "┐", fg=CASE)
    if title:
        c.put(2, 0, f" {title} ", fg=YELLOW, bold=True)
    for i, line in enumerate(body, 1):
        c.put(0, i, "│", fg=CASE)
        c.put_ansi(2, i, line, width, "")
        c.put(width + 3, i, "│", fg=CASE)
    c.put(0, len(body) + 1, "└" + "─" * (width + 2) + "┘", fg=CASE)
    return c.render()


def _key_button_rows(keys, width):
    """Lay key buttons out as an even grid that fills `width`: every button the
    same width, labels centred, like the full device's buttons.
    Returns rows of [(x, text)]."""
    keys = list(keys)
    if not keys:
        return []
    widest = max(len(f"{k} {label}") for k, label in keys) + 2
    per_row = max(1, min(len(keys), (width + 1) // (widest + 1)))
    cell = (width - (per_row - 1)) // per_row
    rows = []
    for start in range(0, len(keys), per_row):
        row = []
        for i, (k, label) in enumerate(keys[start:start + per_row]):
            text = f"{k} {label}"[:cell]
            row.append((i * (cell + 1), text.center(cell)))
        rows.append(row)
    return rows


def _slim(lines, width, info, keys, slots, lights, status):
    """The Pokedex for split or narrow terminals: the same red body, bezelled
    screen and blue key buttons, with the lens and lights tucked into the top
    edge and the controls replaced by the keys. About 7 rows of frame instead
    of 16, so it fits where the full device can't."""
    cols, rows = terminal_size()
    side = 1
    total_w = width + 2 * (side + FRAME)
    key_rows = _key_button_rows(list(keys), total_w - 4)
    key_h = max(1, 2 * len(key_rows) - 1)                # a gap between rows, like the full device
    room = rows - 1 - SLIM_CHROME - key_h
    body = list(lines)
    extra = [l for l in info if CSI_RE.sub("", l).strip()]
    if extra and len(body) + 1 + len(extra) <= room:
        body += [""] + extra                           # the info block too, when there's room
    if len(body) > room:
        body = body[: max(1, room - 1)] + ["\x1b[38;5;245m(make the window taller to see more)\x1b[0m"]
    height = len(body)
    total_h = height + SLIM_CHROME + key_h
    c = Canvas(total_w, total_h)
    c.fill(0, 0, total_w, total_h, CASE)
    c.put(0, 0, "▀" * total_w, fg=CASE_DARK, bg=CASE)              # top edge
    c.fill(0, 0, 1, total_h, CASE_DARK)
    c.fill(total_w - 1, 0, 1, total_h, CASE_DARK)
    c.fill(0, total_h - 1, total_w, 1, CASE_DARK)
    # lens and lights, small, in the top strip
    c.put(2, 1, "▐", fg=255, bg=CASE)
    c.put(3, 1, "▀", fg=153, bg=BLUE)
    c.put(4, 1, " ", bg=BLUE)
    c.put(5, 1, "▌", fg=255, bg=CASE)
    for i, (on, col, off) in enumerate(zip(lights, (LIGHT_RED, LIGHT_YELLOW, LIGHT_GREEN), (88, 94, 22))):
        c.put(8 + i * 3, 1, "■", fg=col if on else off, bg=CASE)
    label = "  ".join(x for x in slots if x)
    c.put(total_w - 2 - len(label[: total_w - 20]), 1, label[: total_w - 20], fg=255, bg=CASE, bold=True)
    # bezel and screen
    bx, by = side, 2
    bw = width + 2 * FRAME
    c.fill(bx, by, bw, height + 4, BEZEL)
    sx, sy, sw = bx + 2, by + 2, width + 2
    c.put(bx + bw // 2 - 2, by, "⊕ ⊕", fg=LIGHT_RED, bg=BEZEL)
    c.put(sx - 1, sy - 1, "▗" + "▄" * sw + "▖", fg=SCREEN_EDGE, bg=BEZEL)
    for y in range(sy, sy + height):
        c.put(sx - 1, y, "▐", fg=SCREEN_EDGE, bg=BEZEL)
        c.put(sx + sw, y, "▌", fg=SCREEN_EDGE, bg=BEZEL)
    c.put(sx - 1, sy + height, "▝" + "▀" * sw + "▘", fg=SCREEN_EDGE, bg=BEZEL)
    c.fill(sx, sy, sw, height, SCREEN)
    base = sgr(SCREEN_FG, SCREEN)
    for i, line in enumerate(body):
        c.put_ansi(bx + FRAME, sy + i, line, width, base)
    c.put(bx + 2, by + height + 3, "●", fg=LIGHT_RED, bg=BEZEL)
    if status:
        c.put(bx + 4, by + height + 3, fit_words(status, bw - 14), fg=BEZEL_DARK, bg=BEZEL)
    c.put(bx + bw - 7, by + height + 3, "≡≡≡≡≡", fg=BEZEL_DARK, bg=BEZEL)
    # the key buttons, where the controls would be
    ky = by + height + 5
    for r, row in enumerate(key_rows):
        for x, text in row:
            c.put(2 + x, ky + 2 * r, text, fg=BLUE_DARK, bg=BLUE, bold=True)
    return c.render()


def right_height(info, keys):
    """Rows the right half needs for this info and these keys."""
    key_rows = (min(len(keys), MAX_KEYS) + 2) // 3
    return 3 + 5 + (len(info) + 2) + key_rows * 2 + 9


def _device(lines, width, info, keys, slots, lights, status, mode):
    left_w = width + 2 * (SIDE + FRAME)
    if mode == "full":
        # Make the screen taller (blank rows) if the right half needs the room.
        need = right_height(info, keys) - (TOP_ROWS + BEZEL_ROWS + BOTTOM_ROWS)
        lines = list(lines) + [""] * max(0, need - len(lines))
    height = len(lines)
    total_h = TOP_ROWS + height + BEZEL_ROWS + BOTTOM_ROWS
    right = mode == "full"
    total_w = left_w + (HINGE + RIGHT_WIDTH if right else 0)
    extra = 0 if right else 3                      # half mode: keys/info under the device
    c = Canvas(total_w, total_h + extra)

    # ---- left half: body --------------------------------------------------
    c.fill(0, 0, left_w, total_h, CASE)
    c.fill(0, total_h - 1, left_w, 1, CASE_DARK)                 # bottom edge
    c.fill(left_w - 1, 0, 1, total_h, CASE_DARK)                 # right edge shading
    c.fill(0, 0, 1, total_h, CASE_DARK)                          # left edge
    c.put(0, 0, "▀" * left_w, fg=CASE_DARK, bg=CASE)              # top edge
    # lens: a blue disc with a white rim and a glint
    c.put(4, 1, "▗▄▄▄▄▖", fg=255, bg=CASE)
    for y in (2, 3):
        c.put(3, y, "▐", fg=255, bg=CASE)
        c.fill(4, y, 6, 1, BLUE)
        c.put(10, y, "▌", fg=255, bg=CASE)
    c.put(4, 2, "▘", fg=255, bg=BLUE)                             # rim follows the corners
    c.put(9, 2, "▝", fg=255, bg=BLUE)
    c.put(4, 3, "▖", fg=255, bg=BLUE)
    c.put(9, 3, "▗", fg=255, bg=BLUE)
    c.put(6, 2, "▀", fg=153, bg=BLUE)                             # glint
    c.put(4, 4, "▝▀▀▀▀▘", fg=255, bg=CASE)
    # three lights: red, yellow, green (dim when off)
    for i, (on, col, off) in enumerate(zip(lights, (LIGHT_RED, LIGHT_YELLOW, LIGHT_GREEN), (88, 94, 22))):
        c.put(13 + i * 4, 1, "██", fg=col if on else off, bg=CASE)
    # the seam under the lens, stepping up towards the hinge
    seam_y = TOP_ROWS - 1
    step_x = max(24, left_w - 14)
    c.put(0, seam_y, "▀" * step_x, fg=CASE_LINE, bg=CASE)
    for i in range(3):
        c.put(step_x + i * 2, seam_y - i - 1, "▄▀", fg=CASE_LINE, bg=CASE)
    c.put(step_x + 6, seam_y - 4, "▀" * max(0, left_w - step_x - 7), fg=CASE_LINE, bg=CASE)

    # ---- screen bezel and screen --------------------------------------------
    bx, by = SIDE, TOP_ROWS
    bw, bh = width + 2 * FRAME, height + BEZEL_ROWS
    c.fill(bx, by, bw, bh, BEZEL)
    mid = bx + bw // 2
    c.put(mid - 2, by, "⊕ ⊕", fg=LIGHT_RED, bg=BEZEL)
    # The screen, set into the bezel: a thin dark edge (half a cell, drawn
    # with half-blocks) so the black doesn't cut straight into the white.
    sx, sy, sw = bx + 2, by + 2, width + 2
    c.put(sx - 1, sy - 1, "▗" + "▄" * sw + "▖", fg=SCREEN_EDGE, bg=BEZEL)
    for y in range(sy, sy + height):
        c.put(sx - 1, y, "▐", fg=SCREEN_EDGE, bg=BEZEL)
        c.put(sx + sw, y, "▌", fg=SCREEN_EDGE, bg=BEZEL)
    c.put(sx - 1, sy + height, "▝" + "▀" * sw + "▘", fg=SCREEN_EDGE, bg=BEZEL)
    c.fill(sx, sy, sw, height, SCREEN)
    base = sgr(SCREEN_FG, SCREEN)
    for i, line in enumerate(lines):
        c.put_ansi(bx + FRAME, sy + i, line, width, base)
    c.put(bx + 2, by + bh - 1, "●", fg=LIGHT_RED, bg=BEZEL)
    c.put(bx + bw - 8, by + bh - 1, "≡≡≡≡≡", fg=BEZEL_DARK, bg=BEZEL)

    # ---- controls under the screen ------------------------------------------
    cy = by + bh + 1
    c.put(SIDE, cy, "▄██▄", fg=BLUE, bg=CASE)                      # round button
    c.put(SIDE, cy + 1, "▀██▀", fg=BLUE, bg=CASE)
    c.put(SIDE + 6, cy, "▄▄▄▄", fg=CASE_LINE, bg=CASE)              # two pills
    c.put(SIDE + 12, cy, "▄▄▄▄", fg=GREEN_BTN, bg=CASE)
    status_w = min(max(14, len(status) + 4), max(14, left_w - 26))
    c.fill(SIDE + 6, cy + 2, status_w, 2, GREEN_BTN)               # green status slot
    c.put(SIDE + 7, cy + 2, status[: status_w - 2], fg=GREEN_DARK, bg=GREEN_BTN, bold=True)
    dx = left_w - SIDE - 9                                          # d-pad
    c.put(dx + 3, cy, "██", fg=BLUE, bg=CASE)
    c.put(dx, cy + 1, "███", fg=BLUE, bg=CASE)
    c.put(dx + 3, cy + 1, "▪▪", fg=BLUE_DARK, bg=BLUE)
    c.put(dx + 5, cy + 1, "███", fg=BLUE, bg=CASE)
    c.put(dx + 3, cy + 2, "██", fg=BLUE, bg=CASE)

    if right:
        _right_half(c, left_w, total_h, info, keys, slots)
    else:
        # half mode: what the right half would show goes under the device
        summary = "  ".join(CSI_RE.sub("", l).strip() for l in info[:2] if CSI_RE.sub("", l).strip())
        c.put_ansi(1, total_h, summary, total_w - 2, "")
        c.put_ansi(1, total_h + 1, " · ".join(s for s in slots if s), total_w - 2, "")
        c.put_ansi(1, total_h + 2, _key_text(keys), total_w - 2, "")
    return c.render()


def _right_half(c, left_w, total_h, info, keys, slots):
    x0 = left_w + HINGE
    top = 3
    # hinge between the halves
    c.fill(left_w, top, HINGE, total_h - top, CASE_DARK)
    for y in (top + 2, top + 4, total_h - 4, total_h - 2):
        c.put(left_w, y, "▀" * HINGE, fg=CASE_LINE, bg=CASE_DARK)
    # body, with the stepped top-left edge
    c.fill(x0, top, RIGHT_WIDTH, total_h - top, CASE)
    c.fill(x0, total_h - 1, RIGHT_WIDTH, 1, CASE_DARK)
    c.fill(x0 + RIGHT_WIDTH - 1, top, 1, total_h - top, CASE_DARK)
    c.put(x0, top, "▀" * 6, fg=CASE_LINE, bg=CASE)
    for i in range(3):
        c.put(x0 + 6 + i * 2, top + i, "▀▄", fg=CASE_LINE, bg=CASE)
    c.put(x0 + 12, top + 3, "▀" * (RIGHT_WIDTH - 13), fg=CASE_LINE, bg=CASE)

    # green info display: grows to fill the space above the keys
    gx, gy, gw = x0 + 3, top + 5, RIGHT_WIDTH - 6
    key_rows = (min(len(keys), MAX_KEYS) + 2) // 3
    gh = max(5, total_h - gy - key_rows * 2 - 9)
    c.fill(gx, gy, gw, gh, GREEN)
    base = sgr(GREEN_DARK, GREEN)
    for i, line in enumerate(info[: gh - 2]):
        c.put_ansi(gx + 2, gy + 1 + i, line, gw - 4, base)

    # blue key buttons, three per row
    ky = gy + gh + 1
    cell = (gw - 2) // 3
    for i, (key, label) in enumerate(list(keys)[:MAX_KEYS]):
        r, col = divmod(i, 3)
        x = gx + col * (cell + 1)
        y = ky + r * 2
        c.fill(x, y, cell, 1, BLUE)
        text = f"{key} {label}"[:cell]
        c.put(x + max(0, (cell - len(text)) // 2), y, text, fg=BLUE_DARK, bg=BLUE, bold=True)
    rows_used = (min(len(keys), MAX_KEYS) + 2) // 3
    # small buttons and the two dark slots
    sy = max(ky + rows_used * 2, total_h - 6)
    c.put(gx + gw - 18, sy, "▄▄▄▄▄", fg=GREEN_BTN, bg=CASE)
    c.put(gx + gw - 10, sy, "▄▄▄▄▄", fg=YELLOW, bg=CASE)
    c.put(gx + gw - 3, sy + 1, "▐█▌", fg=YELLOW, bg=CASE)
    slot_w = (gw - 4) // 2
    for i, text in enumerate(slots[:2]):
        x = gx + i * (slot_w + 4)
        c.fill(x, total_h - 4, slot_w, 2, SLOT)
        c.put(x + 1, total_h - 4, fit_words(str(text), slot_w - 2), fg=SLOT_FG, bg=SLOT, bold=True)


# -- typing on the device -------------------------------------------------------
def prompt(lines, question, width=44, info=None, keys=(("⏎", "OK"),), slots=("", ""), status="",
           max_len=24, allowed=None):
    """Ask a question on the device's screen and return what was typed.

    `lines` are shown above the question (e.g. instructions, earlier answers,
    an error). Typing works like a normal prompt: letters appear on the
    screen, Backspace deletes, Enter answers. When there's no terminal to
    draw on (input piped in), it falls back to a plain input()."""
    if not sys.stdin.isatty():
        return input(f"{question} ")
    import readchar
    text = ""
    while True:
        cursor = "\x1b[7m \x1b[27m"
        body = list(lines) + ["", f"\x1b[1m{question}\x1b[22m", f" > {text}{cursor}"]
        show(body, width=width, info=info, keys=keys, slots=slots, status=status)
        key = readkey()
        if key is None:
            continue
        if key in (readchar.key.ENTER, "\n"):
            return text
        if key in ("\x7f", "\b"):
            text = text[:-1]
        elif key == "\x03":
            raise KeyboardInterrupt
        elif len(key) == 1 and key.isprintable() and len(text) < max_len and (allowed is None or allowed(key)):
            text += key


class _Resized(Exception):
    pass


def readkey():
    """readchar.readkey(), but returns None as soon as the window is resized,
    so a page can redraw itself to fit straight away."""
    import signal
    import readchar

    def resized(signum, frame):
        raise _Resized()

    try:
        old = signal.signal(signal.SIGWINCH, resized)
    except (ValueError, OSError):           # not the main thread: just read
        return readchar.readkey()
    try:
        return readchar.readkey()
    except _Resized:
        return None
    finally:
        signal.signal(signal.SIGWINCH, old)
