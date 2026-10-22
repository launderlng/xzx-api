"""
XZX shared UI — v7  (mono theme: true black/white, one layer, no colour noise)
"""
import tkinter as tk
import os, sys, random, math, time as _time

# ── palette ───────────────────────────────────────────────────────────────────
# Back to flat black and white — no navy, no cyan, no gradient-as-decoration.
# Structure (borders, weight, spacing) carries the hierarchy instead of colour.
# ACCENT2 is kept a notch off pure white (not identical to ACCENT) purely so
# every button's hover state — computed as a lerp from its own bg toward
# white — still has somewhere to go; identical values there is what made
# hovering invisible in an earlier pass. PANEL et al stay the lighter-than-
# true-black values from the contrast fix, for the same reason: a rounded
# card needs to actually read as a card against the background.
BG       = "#000000"          # true black
GRAD1    = "#000000"
GRAD2    = "#0a0a0a"
GRAD3    = "#000000"
PANEL    = "#141414"
PANEL2   = "#0a0a0a"
PANEL3   = "#1c1c1c"
ACCENT   = "#ffffff"          # pure white — the one "bold" colour is no colour at all
ACCENT2  = "#dcdcdc"          # a hair off pure white, so hover has somewhere to brighten to
ACCENT3  = "#4d4d4d"
FG       = "#ffffff"
FG2      = "#c2c2c2"
MUTED    = "#6b6b6b"
BORDER   = "#333333"
BORDER2  = "#2a2a2a"
GREEN    = "#3ddc74"
GREEN2   = "#27b65c"
RED      = "#ff4d5e"
RED2     = "#e0313f"
CYAN     = "#b3b3b3"          # second macro accent — light grey, distinct from pure white
ORANGE   = "#ffffff"
YELLOW   = "#ffffff"
TAB_UL   = ACCENT             # tab underline

SIDEBAR_W = 52
HEADER_H  = 48
RADIUS    = 16                # card corner radius

FONT_TITLE  = ("Segoe UI Semibold", 13)
FONT_BODY   = ("Segoe UI", 10)
FONT_SMALL  = ("Segoe UI", 9)
FONT_MICRO  = ("Segoe UI", 8)
FONT_MONO   = ("Consolas", 9)
FONT_BADGE  = ("Segoe UI Black", 20)
FONT_LABEL  = ("Segoe UI", 7)
FONT_ICON   = ("Segoe UI Emoji", 15)
FONT_H1     = ("Segoe UI Black", 24)
FONT_H2     = ("Segoe UI Black", 18)


def apply_theme(root: tk.Tk):
    root.configure(bg=BG)
    try: root.tk.call("tk", "scaling", 1.0)
    except Exception: pass


def primary_screen_size(root: tk.Tk):
    """The PRIMARY monitor's resolution, for centering windows.
    Tk's own winfo_screenwidth()/height() return the bounding box of the
    whole Windows virtual desktop when there's more than one monitor —
    so on a two-monitor setup a window 'centered' with those numbers lands
    on the seam between monitors, not the middle of either screen. On
    Windows we ask the OS directly for just the primary monitor's size;
    anywhere else (or if that call fails) we fall back to Tk's numbers."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            try: user32.SetProcessDPIAware()
            except Exception: pass
            w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
            h = user32.GetSystemMetrics(1)   # SM_CYSCREEN
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    return root.winfo_screenwidth(), root.winfo_screenheight()


def round_window(root: tk.Tk, w: int, h: int, radius: int = 22):
    """
    Clip a borderless Tk window (overrideredirect) to real rounded
    corners using the Windows GDI region API, so the desktop shows
    through cleanly at the corners instead of a square edge with a
    rounded panel floating inside it. No-op on non-Windows.
    """
    try:
        import ctypes
        root.update()  # force the native window to exist before we grab its handle
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, w + 1, h + 1, radius, radius)
        ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
    except Exception:
        pass


class Draggable:
    """Mixin for frameless (overrideredirect) windows: bind any widget so
    dragging it moves self.root. Shared by the login window, the Hub, and
    now every macro window too, so they all behave the same way."""
    def _make_draggable(self, widget):
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>",     self._drag_motion)
    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()
    def _drag_motion(self, e):
        self.root.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")


def frameless(root: tk.Tk, w: int, h: int, radius: int = 20, topmost: bool = True):
    """Strip the OS title bar, clip to rounded corners, and pin the window
    above everything else — the single chrome setup every XZX window
    (login, Hub, and the three macros) now shares, instead of some windows
    keeping the native Windows title bar and others not."""
    root.overrideredirect(True)
    root.configure(bg=BG)
    if topmost:
        try: root.attributes("-topmost", True)
        except Exception: pass
    root.after(60, lambda: round_window(root, w, h, radius))


class Snowfall:
    """A light snowfall drifting down a window's background canvas — the
    one continuous bit of motion behind an otherwise static dark panel.
    Independent per window/canvas; create after the canvas exists, call
    .start() once, .stop() on window close so the .after() loop doesn't
    keep firing against a destroyed widget."""
    def __init__(self, canvas, w, h, count=55):
        self.cv, self.w, self.h = canvas, w, h
        self._running = False
        self._job = None
        self._flakes = [self._new_flake(y=random.uniform(0, h)) for _ in range(count)]

    def _new_flake(self, y=None):
        r = random.choice([1, 1, 1, 2, 2, 3])
        return {
            "x": random.uniform(0, self.w),
            "y": y if y is not None else -4,
            "r": r,
            "vy": random.uniform(0.15, 0.45) * (1 + r * 0.12),
            "sway": random.uniform(0.3, 1.0),
            "phase": random.uniform(0, 2 * math.pi),
            "shade": random.choice([MUTED, FG2, FG2, FG]),
            "id": None,
        }

    def start(self):
        if self._running:
            return
        self._running = True
        for f in self._flakes:
            f["id"] = self.cv.create_oval(0, 0, 0, 0, fill=f["shade"], outline="")
        self._tick()

    def stop(self):
        self._running = False
        if self._job:
            try: self.cv.after_cancel(self._job)
            except Exception: pass
            self._job = None

    def _tick(self):
        if not self._running:
            return
        try:
            t = _time.time()
            for f in self._flakes:
                f["y"] += f["vy"]
                if f["y"] > self.h + 4:
                    f.update(self._new_flake(y=-4))
                    self.cv.itemconfigure(f["id"], fill=f["shade"])
                sx = f["x"] + math.sin(t * f["sway"] + f["phase"]) * 7
                r = f["r"]
                self.cv.coords(f["id"], sx - r, f["y"] - r, sx + r, f["y"] + r)
            self._job = self.cv.after(40, self._tick)
        except tk.TclError:
            self._running = False  # canvas was destroyed mid-tick


def get_monitors():
    """List connected physical monitors as (label, left, top, width, height)
    tuples, using mss (already an XZX dependency). Lets a macro's capture
    region be pointed at whichever screen the game is actually on, instead
    of always assuming the primary monitor."""
    try:
        import mss
        with mss.mss() as sct:
            mons = sct.monitors[1:]  # [0] is the combined virtual desktop
        if not mons:
            raise ValueError("no monitors reported")
        return [(f"Monitor {i+1}  ({m['width']}x{m['height']})",
                  m["left"], m["top"], m["width"], m["height"])
                for i, m in enumerate(mons)]
    except Exception:
        return [("Monitor 1", 0, 0, 1920, 1080)]


# ── global hotkeys ─────────────────────────────────────────────────────────────
# A Tk <bind> only fires while the macro's own window has keyboard focus,
# which is useless in practice — you're tabbed into the game, not the
# macro. These poll the real OS key state instead, so Start/Stop/Close
# work no matter which window is focused, the same way a normal in-game
# hotkey does.
VK_F5, VK_F6, VK_F7, VK_F8, VK_F9, VK_F10 = 0x74, 0x75, 0x76, 0x77, 0x78, 0x79

def _key_is_down(vk):
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


class HotkeyWatcher:
    """Edge-detects global key presses across repeated .poll() calls (e.g.
    from a Tk .after() tick) so each physical press fires its action once,
    not once per 50ms tick for as long as the key is held."""
    def __init__(self):
        self._down = {}

    def pressed(self, vk):
        now = _key_is_down(vk)
        fired = now and not self._down.get(vk, False)
        self._down[vk] = now
        return fired


# ── theme persistence (full-palette customisation, shared across the Hub
# and every macro window, each running as its own separate process) ───────────
import json as _json
from pathlib import Path as _Path

def suite_dir():
    """The folder every XZX exe/script lives in together. Never __file__
    for a frozen --onefile build — that resolves inside a throwaway
    per-launch extraction folder, not the exe's real, stable folder (the
    same distinction xzx_launcher.py's _launch() already draws)."""
    if getattr(sys, "frozen", False):
        return _Path(sys.executable).parent
    return _Path(__file__).parent

THEME_PATH = suite_dir() / "xzx_theme.json"

_PALETTE_KEYS = ["BG","GRAD1","GRAD2","GRAD3","PANEL","PANEL2","PANEL3",
                 "ACCENT","ACCENT2","ACCENT3","FG","FG2","MUTED",
                 "BORDER","BORDER2","GREEN","GREEN2","RED","RED2","CYAN",
                 "ORANGE","YELLOW"]

def load_theme_overrides():
    """Saved colour overrides from disk, filtered to known palette keys
    with string (hex) values — never trusts the file blindly."""
    try:
        data = _json.loads(THEME_PATH.read_text())
        return {k: v for k, v in data.items()
                if k in _PALETTE_KEYS and isinstance(v, str)}
    except Exception:
        return {}

def save_theme_overrides(values: dict) -> bool:
    try:
        THEME_PATH.write_text(_json.dumps(values, indent=2))
        return True
    except Exception:
        return False

def theme_mtime():
    try:
        return THEME_PATH.stat().st_mtime
    except Exception:
        return 0

def apply_theme_overrides():
    """Push saved overrides into this module's palette constants. Run once
    below so the very first window built already reflects a saved theme —
    including every StatTile/SideIconBtn/etc. default that's *bound* to a
    palette constant, not just call-sites that pass ui.ACCENT explicitly,
    since this runs before any of those classes/functions are defined."""
    overrides = load_theme_overrides()
    g = globals()
    for k in _PALETTE_KEYS:
        g[k] = overrides.get(k, g[k])
    g["TAB_UL"] = g["ACCENT"]

apply_theme_overrides()

def reload_theme_module():
    """A colour change needs more than re-running apply_theme_overrides():
    every class/function above with a palette constant as a *default
    argument* (StatTile's accent=ACCENT, SideIconBtn's active_color=ACCENT,
    draw_gradient's c1=GRAD1, …) bound that default at definition time, so
    reassigning the bare global afterwards doesn't reach them. Reloading
    this whole module re-executes it top to bottom — literals reset, then
    apply_theme_overrides() re-applies the saved colours, then every def
    rebinds its defaults fresh. importlib.reload() mutates this module
    object in place, so every `ui.xxx` a caller already holds (including
    `import xzx_ui as ui` itself) sees the refreshed values with no need
    to re-import."""
    import importlib
    importlib.reload(sys.modules[__name__])


# ── scrollable settings list (for panels with more rows than fit) ─────────────
def scroll_frame(parent, bg=PANEL):
    """A vertically-scrollable area: pack/place the returned outer frame,
    then add content to the returned inner frame as if it were a plain one."""
    outer = tk.Frame(parent, bg=bg)
    cv = tk.Canvas(outer, bg=bg, highlightthickness=0)
    sb = tk.Scrollbar(outer, orient="vertical", command=cv.yview,
                      bg=PANEL2, troughcolor=PANEL, activebackground=ACCENT2,
                      relief="flat", width=8)
    cv.configure(yscrollcommand=sb.set)
    cv.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    inner = tk.Frame(cv, bg=bg)
    win = cv.create_window((0, 0), window=inner, anchor="nw")
    def _on_inner_config(e): cv.configure(scrollregion=cv.bbox("all"))
    def _on_canvas_config(e): cv.itemconfigure(win, width=e.width)
    inner.bind("<Configure>", _on_inner_config)
    cv.bind("<Configure>", _on_canvas_config)
    def _wheel(e):
        cv.yview_scroll(-1 * (1 if e.delta > 0 else -1), "units")
    # Bind/unbind the wheel only while the pointer is actually over this
    # canvas (not bind_all) — otherwise scrolling anywhere else in the
    # window, including other tabs, would hijack this scrollbar.
    def _enter(e): cv.bind_all("<MouseWheel>", _wheel)
    def _leave(e): cv.unbind_all("<MouseWheel>")
    cv.bind("<Enter>", _enter)
    cv.bind("<Leave>", _leave)
    return outer, inner


# ── gradient canvas helper ────────────────────────────────────────────────────
def draw_gradient(canvas, w, h, c1=GRAD1, c2=GRAD3, steps=60, diagonal=False):
    """Fill canvas with a top→bottom (or diagonal) gradient."""
    r1,g1,b1 = canvas.winfo_rgb(c1)
    r2,g2,b2 = canvas.winfo_rgb(c2)
    for i in range(steps):
        t  = i / steps
        r  = int(r1 + (r2-r1)*t) >> 8
        g  = int(g1 + (g2-g1)*t) >> 8
        b  = int(b1 + (b2-b1)*t) >> 8
        col = f"#{r:02x}{g:02x}{b:02x}"
        if diagonal:
            # thin diagonal band — approximate with overlapping parallelograms
            off = int(w * i / steps)
            canvas.create_polygon(
                -w+off, 0, w, 0, w, h, -w+off, h,
                fill=col, outline="")
        else:
            y0  = int(h * i / steps)
            y1  = int(h * (i+1) / steps) + 1
            canvas.create_rectangle(0, y0, w, y1, fill=col, outline="")


def _rounded_pts(x0, y0, x1, y1, r):
    """Point list for a rounded-rectangle polygon (smooth=True)."""
    r = min(r, (x1-x0)/2, (y1-y0)/2)
    return [
        x0+r, y0,   x1-r, y0,   x1, y0,   x1, y0+r,
        x1, y1-r,   x1, y1,     x1-r, y1, x0+r, y1,
        x0, y1,     x0, y1-r,   x0, y0+r, x0, y0,
    ]


def round_rect(canvas, x0, y0, x1, y1, r=14, **kw):
    """Draw a smooth rounded rectangle on a canvas."""
    return canvas.create_polygon(_rounded_pts(x0, y0, x1, y1, r),
                                  smooth=True, splinesteps=24, **kw)


def _lerp_color(widget, c1, c2, t):
    """Blend two hex colours (t=0 -> c1, t=1 -> c2) using Tk's own RGB
    resolver so it works for any colour name/hex Tk accepts."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = widget.winfo_rgb(c1)
    r2, g2, b2 = widget.winfo_rgb(c2)
    r = int(r1 + (r2 - r1) * t) >> 8
    g = int(g1 + (g2 - g1) * t) >> 8
    b = int(b1 + (b2 - b1) * t) >> 8
    return f"#{r:02x}{g:02x}{b:02x}"


def glow(canvas, cx, cy, radius, colour=None, rings=3):
    """A soft radial halo behind an active element — concentric stippled
    circles fading outward. Tk canvases have no real alpha blending, so
    this dithers it instead; it reads as a glow at the sizes used here."""
    colour = colour or ACCENT
    stipples = ["gray12", "gray25", "gray50"][:rings]
    # draw largest/faintest first so smaller/denser rings sit on top
    sizes = [radius * (1 + 0.6 * (rings - i) / rings) for i in range(rings)]
    for size, stip in zip(sorted(sizes, reverse=True), stipples[::-1]):
        canvas.create_oval(cx - size, cy - size, cx + size, cy + size,
                            fill=colour, outline="", stipple=stip)


# ── rounded card ─────────────────────────────────────────────────────────────
class RoundFrame(tk.Canvas):
    """A Canvas that draws a rounded-rect background and otherwise behaves
    like an ordinary Frame: pack/grid it, it resizes with its geometry
    manager, and you add children to `.body` (a plain tk.Frame) exactly as
    you would to any other frame. Used for stat tiles, log panels and any
    other 'card' that used to be a square-cornered tk.Frame."""
    def __init__(self, parent, bg=PANEL, border=BORDER2, border_w=1, r=14):
        pbg = parent["bg"]
        super().__init__(parent, bg=pbg, highlightthickness=0)
        self._bg, self._border, self._border_w, self._r = bg, border, border_w, r
        self.body = tk.Frame(self, bg=bg)
        self._win = self.create_window(0, 0, anchor="nw", window=self.body)
        self.bind("<Configure>", self._redraw)

    def sync_size(self):
        """Call once after populating .body, before this widget is packed/gridded.
        A Canvas doesn't auto-size to its children the way a Frame does, so this
        copies body's natural (requested) size onto the canvas — giving it the
        same initial footprint a plain tk.Frame would have had. A parent that
        packs this with fill/expand still stretches it normally afterwards,
        via the <Configure> binding below."""
        self.body.update_idletasks()
        inset = max(self._border_w, 1) + 1
        w = self.body.winfo_reqwidth() + 2 * inset
        h = self.body.winfo_reqheight() + 2 * inset
        self.configure(width=w, height=h)

    def _redraw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("bg")
        kw = {"fill": self._bg, "tags": "bg"}
        if self._border:
            kw["outline"] = self._border
            kw["width"] = self._border_w
        else:
            kw["outline"] = ""
        round_rect(self, 1, 1, w - 1, h - 1, self._r, **kw)
        self.tag_lower("bg")
        inset = max(self._border_w, 1) + 1
        self.coords(self._win, inset, inset)
        self.itemconfigure(self._win, width=max(w - 2 * inset, 1), height=max(h - 2 * inset, 1))


# ── hotkey keycap chip ────────────────────────────────────────────────────────
def hotkey_chip(parent, key, label, bg=PANEL):
    """A small rounded 'keycap' badge (e.g. F5) plus its action word, replacing
    the old square-cornered bordered label used in the hotkey row under the
    Start/Stop button on every macro screen."""
    kg = tk.Frame(parent, bg=bg)
    font = ("Consolas", 8, "bold")
    tmp = tk.Label(kg, text=key, font=font)
    tmp.update_idletasks()
    tw, th = tmp.winfo_reqwidth(), tmp.winfo_reqheight()
    tmp.destroy()
    pad_x, pad_y = 7, 4
    w, h = tw + pad_x * 2, th + pad_y * 2
    cv = tk.Canvas(kg, width=w, height=h, bg=bg, highlightthickness=0)
    cv.pack(side="left")
    round_rect(cv, 0, 0, w, h, r=8, fill=PANEL3, outline=BORDER, width=1)
    cv.create_text(w // 2, h // 2, text=key, fill=FG2, font=font)
    tk.Label(kg, text=label, bg=bg, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(5, 0))
    return kg


def hotkey_row(parent, pairs, bg=PANEL):
    """pairs: [(key, label), ...]. Returns a frame you pack/place yourself."""
    hr = tk.Frame(parent, bg=bg)
    for i, (k, txt) in enumerate(pairs):
        chip = hotkey_chip(hr, k, txt, bg=bg)
        chip.pack(side="left", padx=(0, 10) if i < len(pairs) - 1 else 0)
    return hr


def round_gradient_card(canvas, x0, y0, x1, y1, c1, c2, r=16, steps=40,
                         outline="", outline_width=0):
    """Rounded rectangle filled with a vertical gradient, via clipped bands."""
    w, h = x1 - x0, y1 - y0
    r1,g1,b1 = canvas.winfo_rgb(c1)
    r2,g2,b2 = canvas.winfo_rgb(c2)
    # paint the gradient, then mask the four corners back to the parent bg
    for i in range(steps):
        t  = i / steps
        rr = int(r1 + (r2-r1)*t) >> 8
        gg = int(g1 + (g2-g1)*t) >> 8
        bb = int(b1 + (b2-b1)*t) >> 8
        col = f"#{rr:02x}{gg:02x}{bb:02x}"
        yy0 = y0 + h * i / steps
        yy1 = y0 + h * (i+1) / steps + 1
        canvas.create_rectangle(x0, yy0, x1, yy1, fill=col, outline="")
    bg = canvas["bg"]
    cr = r
    # corner masks (quarter squares outside the rounded silhouette)
    canvas.create_rectangle(x0, y0, x0+cr, y0+cr, fill=bg, outline="")
    canvas.create_rectangle(x1-cr, y0, x1, y0+cr, fill=bg, outline="")
    canvas.create_rectangle(x0, y1-cr, x0+cr, y1, fill=bg, outline="")
    canvas.create_rectangle(x1-cr, y1-cr, x1, y1, fill=bg, outline="")
    canvas.create_arc(x0, y0, x0+2*cr, y0+2*cr, start=90, extent=90,
                       style="pieslice", fill=col, outline="")
    canvas.create_arc(x1-2*cr, y0, x1, y0+2*cr, start=0, extent=90,
                       style="pieslice", fill=col, outline="")
    canvas.create_arc(x0, y1-2*cr, x0+2*cr, y1, start=180, extent=90,
                       style="pieslice", fill=col, outline="")
    canvas.create_arc(x1-2*cr, y1-2*cr, x1, y1, start=270, extent=90,
                       style="pieslice", fill=col, outline="")
    if outline_width:
        round_rect(canvas, x0, y0, x1, y1, r, fill="", outline=outline,
                    width=outline_width)


def soft_shadow(canvas, x0, y0, x1, y1, r=16, layers=6, colour="#000000"):
    """Cheap drop-shadow: a few descending, expanding, faded rounded rects."""
    base = canvas.winfo_rgb(canvas["bg"])
    sr, sg, sb = [c >> 8 for c in canvas.winfo_rgb(colour)]
    br, bg_, bb = [c >> 8 for c in base]
    for i in range(layers, 0, -1):
        t = i / layers
        rr = int(br + (sr-br)*0.35*t)
        gg = int(bg_ + (sg-bg_)*0.35*t)
        bb2 = int(bb + (sb-bb)*0.35*t)
        col = f"#{rr:02x}{gg:02x}{bb2:02x}"
        pad = i * 1.4
        round_rect(canvas, x0-pad, y0-pad+i*1.6, x1+pad, y1+pad+i*1.6,
                   r+pad, fill=col, outline="")


# ── card frame with gradient bg ───────────────────────────────────────────────
class GradCard(tk.Frame):
    """A frame that paints a gradient canvas behind its children."""
    def __init__(self, parent, w, h, c1=GRAD1, c2=GRAD3, radius=RADIUS, **kw):
        super().__init__(parent, bg=BG, width=w, height=h, **kw)
        self.pack_propagate(False)
        self._cv = tk.Canvas(self, width=w, height=h,
                             highlightthickness=1,
                             highlightbackground=BORDER,
                             bg=GRAD1)
        self._cv.place(x=0, y=0)
        self.bind("<Map>", lambda e: self._paint(w, h, c1, c2))

    def _paint(self, w, h, c1, c2):
        draw_gradient(self._cv, w, h, c1, c2)

    def canvas(self): return self._cv


# ── simple label helpers ──────────────────────────────────────────────────────
def label(parent, text, fg=FG2, font=FONT_BODY, bg=None, **kw):
    b = bg if bg is not None else parent["bg"]
    return tk.Label(parent, text=text, bg=b, fg=fg, font=font, **kw)

def heading(parent, text, **kw):  return label(parent, text, font=FONT_TITLE, **kw)
def muted(parent, text, **kw):    return label(parent, text, fg=MUTED, font=FONT_SMALL, **kw)


# ── icon sidebar button ───────────────────────────────────────────────────────
class SideIconBtn:
    def __init__(self, parent, icon, tooltip, active_color=ACCENT):
        self.active_color = active_color
        self.active = False
        self.frame = tk.Frame(parent, bg=PANEL2, width=SIDEBAR_W, height=SIDEBAR_W)
        self.frame.pack_propagate(False)
        self.lbl = tk.Label(self.frame, text=icon, bg=PANEL2, fg=MUTED,
                            font=FONT_ICON, cursor="hand2")
        self.lbl.place(relx=0.5, rely=0.5, anchor="center")
        # left active bar
        self.bar = tk.Frame(self.frame, bg=PANEL2, width=3)
        self.bar.place(x=0, y=0, relheight=1)

    def set_active(self, v):
        self.active = v
        self.lbl.configure(fg=self.active_color if v else MUTED)
        self.bar.configure(bg=self.active_color if v else PANEL2)
        self.frame.configure(bg=PANEL if v else PANEL2)
        self.lbl.configure(bg=PANEL if v else PANEL2)

    def bind(self, seq, cb):
        self.frame.bind(seq, cb)
        self.lbl.bind(seq, cb)


# ── tab bar ───────────────────────────────────────────────────────────────────
class TabBar:
    def __init__(self, parent, tabs: list, on_change, bg=PANEL):
        self.frame = tk.Frame(parent, bg=bg)
        self._btns = []
        self._on   = on_change
        self._sel  = 0
        for i, name in enumerate(tabs):
            b = tk.Label(self.frame, text=name, bg=bg, fg=MUTED,
                         font=("Segoe UI Semibold", 10), cursor="hand2",
                         padx=16, pady=8)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, idx=i: self._click(idx))
            # underline canvas
            ul = tk.Canvas(self.frame, width=b.winfo_reqwidth(), height=2,
                           bg=bg, highlightthickness=0)
            # packed below via place after render
            self._btns.append((b, ul))
        self.frame.after(50, lambda: self._select(0))

    def _click(self, idx):
        self._select(idx)
        self._on(idx)

    def _select(self, idx):
        self._sel = idx
        for i, (b, ul) in enumerate(self._btns):
            active = i == idx
            b.configure(fg=FG if active else MUTED)
            # draw/clear underline
            bx = b.winfo_x()
            bw = b.winfo_width()
            ul.configure(width=max(bw,1))
            ul.place(x=bx, y=b.winfo_y() + b.winfo_height() - 2)
            ul.delete("all")
            if active:
                ul.create_rectangle(0, 0, bw, 2, fill=ACCENT, outline="")


# ── stat tile ─────────────────────────────────────────────────────────────────
class StatTile:
    def __init__(self, parent, name, value="0", accent=ACCENT, bg=PANEL):
        self.frame = RoundFrame(parent, bg=bg, border=BORDER2, border_w=1, r=18)
        body = self.frame.body
        tk.Frame(body, bg=accent, height=2).pack(fill="x")
        inn = tk.Frame(body, bg=bg)
        inn.pack(fill="both", expand=True, padx=12, pady=8)
        self.var = tk.StringVar(value=value)
        tk.Label(inn, textvariable=self.var, bg=bg, fg=FG,
                 font=FONT_BADGE).pack(anchor="w")
        tk.Label(inn, text=name, bg=bg, fg=MUTED,
                 font=FONT_LABEL).pack(anchor="w")
        self.frame.sync_size()

    def set(self, v): self.var.set(str(v))


# ── glow toggle switch ────────────────────────────────────────────────────────
class Switch:
    """A pill-shaped on/off toggle with a sliding knob and a soft cyan glow
    when on — replaces the plain square tk.Checkbutton used for things like
    'Test mode' or 'Random jitter'. Drop-in enough for a BooleanVar: pass the
    var in, read/set it exactly as you would a Checkbutton's."""
    def __init__(self, parent, variable, command=None, w=42, h=23, bg=None):
        pbg = bg if bg is not None else parent["bg"]
        self.var = variable
        self.cmd = command
        self.w, self.h = w, h
        self.r = h // 2
        self.cv = tk.Canvas(parent, width=w, height=h, bg=pbg,
                            highlightthickness=0, cursor="hand2")
        self._pos = 1.0 if variable.get() else 0.0
        self._target = self._pos
        self._anim_job = None
        self._draw()
        self.cv.bind("<Button-1>", self._toggle)
        try:
            self._trace = self.var.trace_add("write", lambda *a: self._sync())
        except Exception:
            self._trace = None

    def _sync(self):
        self._animate_to(1.0 if self.var.get() else 0.0)

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        self._animate_to(1.0 if self.var.get() else 0.0)
        if self.cmd:
            self.cmd()

    def _animate_to(self, target):
        self._target = target
        if self._anim_job is None:
            self._step()

    def _step(self):
        diff = self._target - self._pos
        if abs(diff) < 0.02:
            self._pos = self._target
            self._draw()
            self._anim_job = None
            return
        self._pos += diff * 0.4
        self._draw()
        self._anim_job = self.cv.after(12, self._step)

    def _draw(self):
        self.cv.delete("all")
        t = self._pos
        track = _lerp_color(self.cv, BORDER2, ACCENT2, t)
        round_rect(self.cv, 1, 1, self.w - 1, self.h - 1, self.r,
                   fill=track, outline=BORDER, width=1)
        kr = self.r - 3
        kx = 1 + self.r + (self.w - 2 * self.r - 2) * t
        ky = self.h / 2
        if t > 0.04:
            glow(self.cv, kx, ky, kr + 9, colour=ACCENT, rings=3)
        self.cv.create_oval(kx - kr, ky - kr, kx + kr, ky + kr,
                            fill="#ffffff", outline="")

    def pack(self, **kw):  self.cv.pack(**kw)
    def place(self, **kw): self.cv.place(**kw)
    def grid(self, **kw):  self.cv.grid(**kw)


# ── rounded pill button (canvas-drawn, matches the rounded-window theme) ───────
class RoundButton:
    def __init__(self, parent, text, command, w=300, h=46, r=12,
                 bg=ACCENT2, hover=ACCENT, fg=BG, disabled_bg=None,
                 font=("Segoe UI Black", 11), parent_bg=None):
        pbg = parent_bg if parent_bg is not None else parent["bg"]
        self.cv = tk.Canvas(parent, width=w, height=h, bg=pbg,
                            highlightthickness=0, cursor="hand2")
        self.w, self.h, self.r = w, h, r
        self.bg, self.hover, self.fg, self.font = bg, hover, fg, font
        self.disabled_bg = disabled_bg or MUTED
        self.text = text
        self._enabled = True
        self._draw(bg)
        self.cv.bind("<Button-1>", lambda e: self._click())
        self.cv.bind("<Enter>",    lambda e: self._enabled and self._draw(hover))
        self.cv.bind("<Leave>",    lambda e: self._enabled and self._draw(bg))
        self._command = command

    def _click(self):
        if self._enabled:
            self._command()

    def _draw(self, colour):
        self.cv.delete("all")
        round_rect(self.cv, 1, 1, self.w - 1, self.h - 1, self.r,
                   fill=colour, outline="")
        self.cv.create_text(self.w / 2, self.h / 2, text=self.text,
                            fill=self.fg, font=self.font)

    def configure(self, state=None, **kw):
        if state == "disabled":
            self._enabled = False
            self.cv.configure(cursor="arrow")
            self._draw(self.disabled_bg)
        elif state == "normal":
            self._enabled = True
            self.cv.configure(cursor="hand2")
            self._draw(self.bg)

    def pack(self, **kw):  self.cv.pack(**kw)
    def place(self, **kw): self.cv.place(**kw)
    def grid(self, **kw):  self.cv.grid(**kw)


# ── toggle button ─────────────────────────────────────────────────────────────
class ToggleButton:
    def __init__(self, parent, on_toggle,
                 start_label="▶  START", stop_label="■  STOP",
                 start_bg=ACCENT2, stop_bg=RED):
        self._running = False
        self._cb = on_toggle
        self._sl = start_label; self._el = stop_label
        self._sbg = start_bg;   self._ebg = stop_bg
        self.btn = tk.Button(parent, text=start_label,
                             bg=start_bg, fg=BG,
                             relief="flat", font=("Segoe UI Black", 11),
                             cursor="hand2", pady=10, command=self._click)
        self.btn.configure(activebackground=_lerp_color(self.btn, start_bg, "#ffffff", 0.35),
                           activeforeground=BG)

    def _click(self):
        self._running = not self._running
        if self._running:
            self.btn.configure(text=self._el, bg=self._ebg, fg="white",
                               activebackground=RED2, activeforeground="white")
        else:
            self.btn.configure(text=self._sl, bg=self._sbg, fg=BG,
                               activebackground=_lerp_color(self.btn, self._sbg, "#ffffff", 0.35),
                               activeforeground=BG)
        self._cb(self._running)

    def force_stop(self):
        if self._running:
            self._running = False
            self.btn.configure(text=self._sl, bg=self._sbg, fg=BG,
                               activebackground=_lerp_color(self.btn, self._sbg, "#ffffff", 0.35),
                               activeforeground=BG)


# ── status badge ──────────────────────────────────────────────────────────────
class StatusBadge:
    def __init__(self, parent, bg=None):
        b = bg or parent["bg"]
        self.frame = tk.Frame(parent, bg=b)
        self._dot = tk.Label(self.frame, text="●", bg=b, fg=MUTED,
                             font=("Segoe UI", 9))
        self._dot.pack(side="left")
        self._lbl = tk.Label(self.frame, text="Stopped", bg=b,
                             fg=MUTED, font=FONT_MICRO)
        self._lbl.pack(side="left", padx=(2,0))

    def set(self, text, colour=MUTED):
        self._dot.configure(fg=colour)
        self._lbl.configure(text=text, fg=colour)


# ── spinner ───────────────────────────────────────────────────────────────────
class Spinner:
    FRAMES = ["⠋","⠙","⠸","⠼","⠴","⠦","⠇","⠏"]
    def __init__(self, label):
        self.lbl=label; self._i=0; self._on=False
    def start(self, text=""):
        self._text=text; self._on=True; self._tick()
    def stop(self):
        self._on=False; self.lbl.configure(text="")
    def _tick(self):
        if not self._on: return
        self.lbl.configure(
            text=f"{self.FRAMES[self._i%len(self.FRAMES)]}  {self._text}")
        self._i+=1; self.lbl.after(80, self._tick)


# ── log box ───────────────────────────────────────────────────────────────────
def log_box(parent, height=12, bg=PANEL2):
    t = tk.Text(parent, height=height, bg=bg, fg=FG2, relief="flat",
                font=FONT_MONO, state="disabled", wrap="word",
                insertbackground=FG, selectbackground=ACCENT2,
                highlightthickness=0, padx=8, pady=6)
    sb = tk.Scrollbar(parent, command=t.yview, bg=PANEL2,
                      troughcolor=PANEL, activebackground=ACCENT2,
                      relief="flat", width=6)
    t.configure(yscrollcommand=sb.set)
    return t, sb

def append_log(box, line, max_lines=400):
    box.configure(state="normal")
    box.insert("end", line+"\n")
    lines = int(box.index("end-1c").split(".")[0])
    if lines > max_lines:
        box.delete("1.0", f"{lines-max_lines}.0")
    box.see("end")
    box.configure(state="disabled")


# ── form widgets ──────────────────────────────────────────────────────────────
def accent_btn(parent, text, cmd, bg=ACCENT2, fg=BG, width=None):
    kw = {"width":width} if width else {}
    hover = _lerp_color(parent, bg, "#ffffff", 0.35)
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                  relief="flat", font=("Segoe UI Semibold",9), cursor="hand2",
                  activebackground=hover, activeforeground=BG,
                  pady=7, **kw)
    b.bind("<Enter>", lambda e: b.configure(bg=hover))
    b.bind("<Leave>", lambda e: b.configure(bg=bg))
    return b

def ghost_btn(parent, text, cmd, width=None):
    kw = {"width":width} if width else {}
    return tk.Button(parent, text=text, command=cmd, bg=PANEL2, fg=MUTED,
                     relief="flat", font=FONT_SMALL, cursor="hand2",
                     activebackground=PANEL3, activeforeground=FG,
                     pady=5, highlightthickness=1,
                     highlightbackground=BORDER2, **kw)

def key_entry(parent, var, width=28, bg=PANEL2):
    return tk.Entry(parent, textvariable=var, bg=bg, fg=FG,
                    insertbackground=ACCENT, justify="center",
                    font=("Consolas",12), width=width, relief="flat",
                    highlightthickness=1, highlightbackground=BORDER,
                    highlightcolor=ACCENT, selectbackground=ACCENT2,
                    selectforeground="white")

def spinbox(parent, var, lo, hi, step=1, width=7):
    return tk.Spinbox(parent, textvariable=var, from_=lo, to=hi,
                      increment=step, width=width, bg=PANEL2, fg=FG,
                      insertbackground=FG, relief="flat",
                      highlightthickness=1, highlightbackground=BORDER2,
                      highlightcolor=ACCENT, font=FONT_SMALL,
                      buttonbackground=PANEL3, disabledbackground=PANEL2)

def dropdown(parent, var, choices, width=7):
    om = tk.OptionMenu(parent, var, *choices)
    om.configure(bg=PANEL2, fg=FG, activebackground=PANEL3,
                 activeforeground=FG, relief="flat",
                 highlightbackground=BORDER2, highlightthickness=1,
                 font=FONT_SMALL, width=width, indicatoron=False, bd=0)
    om["menu"].configure(bg=PANEL2, fg=FG,
                         activebackground=ACCENT2, activeforeground="white",
                         relief="flat", bd=0)
    return om

def row_divider(parent, bg=BORDER2):
    tk.Frame(parent, bg=bg, height=1).pack(fill="x", padx=0, pady=0)

def section_label(parent, text, bg=PANEL):
    f = tk.Frame(parent, bg=bg)
    f.pack(fill="x", padx=16, pady=(14,4))
    tk.Label(f, text=text.upper(), bg=bg, fg=MUTED,
             font=("Segoe UI Semibold", 7)).pack(side="left")
    tk.Frame(f, bg=BORDER2, height=1).pack(side="left", fill="x",
                                           expand=True, padx=(8,0), pady=1)
    return f


def collapsible(parent, title, bg=PANEL, start_open=False):
    """A click-to-expand section: a header row (chevron + title) that shows
    or hides a body frame below it. Used to tuck fine-tuning settings a
    macro doesn't need day to day behind 'Advanced', so the default
    settings view only shows what most people actually touch."""
    state = {"open": start_open}
    wrap = tk.Frame(parent, bg=bg)
    head = tk.Frame(wrap, bg=bg, cursor="hand2")
    head.pack(fill="x", pady=(2, 0))
    chevron = tk.Label(head, text="▾" if start_open else "▸", bg=bg,
                       fg=MUTED, font=("Segoe UI", 9), width=2)
    chevron.pack(side="left")
    lbl = tk.Label(head, text=title, bg=bg, fg=FG2,
                   font=("Segoe UI Semibold", 9), cursor="hand2")
    lbl.pack(side="left")
    body = tk.Frame(wrap, bg=bg)
    if start_open:
        body.pack(fill="x", pady=(6, 0))

    def toggle(_e=None):
        state["open"] = not state["open"]
        chevron.configure(text="▾" if state["open"] else "▸")
        if state["open"]:
            body.pack(fill="x", pady=(6, 0))
        else:
            body.pack_forget()

    head.bind("<Button-1>", toggle)
    lbl.bind("<Button-1>", toggle)
    chevron.bind("<Button-1>", toggle)
    return wrap, body


def macro_header(owner, w: int, icon: str, title: str, accent: str):
    """Shared header for a frameless macro window: icon + title (both
    draggable), a StatusBadge, and minimize/close controls — the exact
    chrome the Hub uses, so a macro window looks and behaves like a child
    of the Hub instead of a different app. `owner` must mix in Draggable
    and expose `.root`; returns the StatusBadge for the caller to drive.
    """
    hdr = tk.Frame(owner.root, bg=PANEL2,
                   highlightbackground=BORDER, highlightthickness=1)
    hdr.place(x=0, y=0, width=w, height=HEADER_H)
    owner._make_draggable(hdr)

    ico = tk.Label(hdr, text=icon, bg=PANEL2, fg=accent, font=("Segoe UI Emoji", 16))
    ico.pack(side="left", padx=(14, 4))
    lbl = tk.Label(hdr, text=title, bg=PANEL2, fg=FG, font=("Segoe UI Black", 12))
    lbl.pack(side="left")
    owner._make_draggable(lbl)

    ctrl = tk.Frame(hdr, bg=PANEL2)
    ctrl.pack(side="right", padx=10)
    badge = StatusBadge(ctrl, bg=PANEL2)
    badge.frame.pack(side="left", padx=(0, 14))

    mb = tk.Label(ctrl, text="─", bg=PANEL2, fg=MUTED, font=("Segoe UI", 12), cursor="hand2")
    mb.pack(side="left", padx=(0, 10))
    mb.bind("<Button-1>", lambda e: owner.root.iconify())
    mb.bind("<Enter>", lambda e: mb.configure(fg=FG))
    mb.bind("<Leave>", lambda e: mb.configure(fg=MUTED))

    cb = tk.Label(ctrl, text="✕", bg=PANEL2, fg=MUTED, font=("Segoe UI", 12), cursor="hand2")
    cb.pack(side="left")
    cb.bind("<Button-1>", lambda e: owner._quit())
    cb.bind("<Enter>", lambda e: cb.configure(fg=RED))
    cb.bind("<Leave>", lambda e: cb.configure(fg=MUTED))

    return badge
