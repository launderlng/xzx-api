"""
XZX Fishing Macro — real cast / skill-check / keep automation (FiveM),
in the black-and-white UI that matches the rest of the Suite.

The detection pipeline (colour masks, template matching for the "E Keep"
prompt, the resolution-independent Screen class, and the cast/skill-check
state machine) is carried over from the calibrated standalone macro —
only the window chrome and the Suite's license gate are new.
"""
import tkinter as tk
import tkinter.messagebox
import base64, os, queue, sys, threading, time, traceback, zlib
import ctypes, ctypes.wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xzx_ui as ui

IS_WIN = sys.platform == "win32"

# ── ANTI-BYPASS: must be launched from XZX Launcher ──────────────────────────
import hmac as _hmac, hashlib as _hs, time as _t2

_SECRET = b"xzx_suite_2025_salt_noclone"

def _get_hwid():
    _p = []
    try:
        import winreg
        _k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            "SOFTWARE\\Microsoft\\Cryptography")
        _p.append(winreg.QueryValueEx(_k, "MachineGuid")[0])
    except Exception: pass
    try:
        import uuid; _p.append(str(uuid.getnode()))
    except Exception: pass
    return _hs.sha256(("|".join(_p) or "fb").encode()).hexdigest()[:32]

def _valid_session(did, hwid, tok):
    for off in (0, -1, 1):
        ts  = str(int(_t2.time() // 300) + off)
        raw = f"{did}:{hwid}:{ts}".encode()
        exp = _hmac.new(_SECRET, raw, _hs.sha256).hexdigest()[:24]
        if _hmac.compare_digest(exp, tok):
            return True
    return False

def _gate():
    # anti-debug check removed — IsDebuggerPresent() false-positives on
    # machines with antivirus/EDR, silently killing the macro with no error.
    _did  = os.environ.get("XZX_DISCORD_ID", "")
    _hwid = _get_hwid()
    _tok  = os.environ.get("XZX_SESSION_TOKEN", "")
    if not _did or not _tok or not _valid_session(_did, _hwid, _tok):
        _r = tk.Tk(); _r.withdraw()
        tkinter.messagebox.showerror("XZX", "Invalid session. Launch from XZX Launcher.")
        _r.destroy(); sys.exit(1)

_gate()
# ─────────────────────────────────────────────────────────────────────────────

WIN_W, WIN_H = 440, 560


def _ensure_deps():
    """mss/numpy drive the whole detection pipeline now (not just an
    optional extra), so make sure they're actually there when running
    from raw python source — BUILD.bat already bundles them into the
    compiled exe, so this only ever fires for a python-source test run."""
    try:
        import mss, numpy  # noqa: F401
        return
    except ImportError:
        pass
    win = tk.Tk()
    win.title("XZX Fishing — setup")
    win.configure(bg="#000000")
    win.geometry("360x110")
    tk.Label(win, text="First-time setup\nInstalling mss + numpy…\n(about a minute)",
             bg="#000000", fg="#ffffff", font=("Segoe UI", 11)).pack(expand=True)
    win.update()
    import subprocess
    flags = 0x08000000 if IS_WIN else 0
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "mss", "numpy"],
                       capture_output=True, text=True, creationflags=flags)
    win.destroy()
    if r.returncode != 0:
        _r = tk.Tk(); _r.withdraw()
        tkinter.messagebox.showerror("XZX Fishing",
                                     "Couldn't install mss/numpy.\n\n" + r.stderr[-800:])
        _r.destroy(); sys.exit(1)
    import site, importlib
    importlib.invalidate_caches()
    try: sys.path.append(site.getusersitepackages())
    except Exception: pass

_ensure_deps()
import numpy as np

S = {
    "rod_key": "1",
    "cast_early": 0.03,   # let go of E this many seconds before green's middle
    "deadzone": 0.20,     # how loosely the blue bar follows the fish
    "fish_lead": 0.04,
    "bar_brake": 0.05,
    "bite_timeout": 120,
    "pause_between": 1.5,
}

# Screen area read (left, top, right, bottom) at 1920x1080
REGION = (660, 830, 1260, 1000)

# ---------------------------------------------------------------------------
# Screen layout inside REGION (from your screenshots, with slack)
# ---------------------------------------------------------------------------

L, T = REGION[0], REGION[1]
CAST_BAND = (898 - T, 942 - T)      # rows of the Cast Power bar
TRACK_BAND = (888 - T, 938 - T)     # rows of the fish track
PROGRESS_BAND = (944 - T, 960 - T)  # rows of the orange catch-progress bar
TRACK_X = (760 - L, 1160 - L)       # fish track, between the Q and E boxes
KEEP_SEARCH = (880 - T, 960 - T, 990 - L, 1140 - L)  # where "E Keep" can be
QBOX_SEARCH = (870 - T, 960 - T, 685 - L, 800 - L)   # skill panel's "Q" box

# "E  Keep" picture taken from your screenshot, used to spot the keep prompt
_KEEP_TPL = (
    "eNrVlN1v0lAYh/8L70RYaaFfQOdalPbUpTB2YbLFmVhXUgZRypiymJEMBzEuGrcLF+OSTacmJC7xQuNg/Trlz7Ma+WoJWb3zuTo358n7nvd3XpZN8KpWnUBThQQbhCSvKTkgjgFySkVIBnHQqgKk/AQSUFQ6iIOq8rm8hxyoUsEcIO8jsEMcXl0eHMR/c+SE2xyb/tvXyMEQMZLGsTh58woO8Bw6fec98DiIr3oTeWfre/QVHMKW2U4tZLx1xD/2iiWrqxLM4AI3w/HEbDOs4HXgH4yVI7uJJqmYC5Ugo1EEp0nMZdTf0FHT7b5zKPoc3W3jBKHi8g8HfpeRhgXt47l9+BNCXaN8dRgtxv+m+Gnv8rIWxQvfPi88sPY37Ma1N+bjLeuLpFlnKON1WIfp7HLeX0ddv3iEPDNM6DhnLcOG0GnW7Fch5eJcof1vynCZKe+xemSehjb1g1Akcv2lWQrdiITrvx3dc5WeNtuO4JuLXlrrmk9Xe6YWbjV2Yedu+Pj+tvlp8QV8i897M3aL49KZrD8fe0jbhjuKDvtOO9KynX7vXt00oN2JJadkfZh5MJlTEosRZBxFUZwmMBTF3Om05jAiOcqHkF2aVCxl+cqM/8KxxC48GPTxx6GtSEC8M4YIpLXKzD9HvYYn5PgOUsobPsrKzB00T8ZH2XdJ8eViQZYfDpHlQrEkpAIt1ISwXvawHnAnu5XQlAc6xf6P/AI4LsmB"
)
# The skill panel's "Q" box, used to make sure it's really the fishing panel
_QBOX_TPL = (
    "eNrt1stqg0AUBuB3iS6sxpnjeBmlFKFk01KwpZasSrQ0UGLiXNQ+fwfEdutxUQj47z/GM8yR33Vdx7Y2s2PZjjvGsaGsZ6aqSpigDXWxpTArlBc12KOzyoIkWTorPCSPpTW6TcXDdHbCtNpMjvL5jtNfV0OKCNSrW921ugiICWVIF7LjYHK+ZSgX+6JTfa86EUQYB+9aNXneKP0BGEcvWhbUf1X6QjGOdEp6MbtTShOk6/ws9rTqyKLztEB9J221GOc7oRx89vKc78x9HlD3ye61lEpJLf0Y4zg8SNW2+ouxDPXOeODthXi68SBG7gPstTxGbUMTnDNP1MzX9S8hcv+CnRy+hxP2PHOp1Gwger71f3Y1DtVDMvrnML0n2U4O2bOKqWfZcHj2F/Q60yPflvTIpb31f/MDvumAKQ=="
)


def _load_tpl(data, shape):
    return np.frombuffer(zlib.decompress(base64.b64decode(data)),
                         dtype=np.uint8).reshape(shape).astype(np.float32)


KEEP_TPL = _load_tpl(_KEEP_TPL, (25, 68))
QBOX_TPL = _load_tpl(_QBOX_TPL, (52, 56))


def _rgb(img):
    return img[..., 0], img[..., 1], img[..., 2]


def mask_green(img):
    r, g, b = _rgb(img)
    return (g > 60) & (g > 1.4 * r + 15) & (g > 1.2 * b)


def mask_yellow(img):
    r, g, b = _rgb(img)
    return (r > 50) & (g > 40) & (r > 2.2 * b) & (g > 2.0 * b) & (r >= 0.95 * g) & (r < 1.5 * g)


def mask_blue(img):
    r, g, b = _rgb(img)
    return (b > 70) & (b > 1.6 * r) & (b > 1.05 * g) & (g > 1.5 * r)


def mask_red(img):
    """The red bar on legendary fish (red with a bit of blue, unlike the orange fish)."""
    r, g, b = _rgb(img)
    return (r > 60) & (r > 1.5 * g) & (r > 1.2 * b) & (b > 0.9 * g)


def mask_bar(img):
    """The player's bar: blue normally, red on legendary fish."""
    return mask_blue(img) | mask_red(img)


def mask_orange(img):
    r, g, b = _rgb(img)
    return (r > 75) & (r > 1.25 * g) & (g > 2.0 * b) & (g > 35)


# ---------------------------------------------------------------------------
# Detectors - each takes the REGION image (H x W x 3, float32 RGB)
# ---------------------------------------------------------------------------

def _runs(cols):
    """Longest run of True in a 1-D bool array -> (start, end) or None."""
    best, start = None, None
    for i, v in enumerate(list(cols) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i - 1)
            start = None
    return best


def find_cast(img):
    """Cast Power bar -> dict with green / yellow x ranges, or None."""
    band = img[CAST_BAND[0]:CAST_BAND[1]]
    g = mask_green(band)
    y = mask_yellow(band)
    if g.sum() < 80 or y.sum() < 400:
        return None
    gx = _runs(g.sum(axis=0) >= 4)
    yx = np.nonzero(y.sum(axis=0) >= 4)[0]
    if gx is None or len(yx) == 0:
        return None
    rows = np.nonzero(g.sum(axis=1) >= 3)[0]
    return {
        "green": gx,
        "yellow": (int(yx.min()), int(yx.max())),
        "rows": (int(rows.min()) + CAST_BAND[0], int(rows.max()) + CAST_BAND[0] + 1),
    }


def find_skill(img, check_panel=True):
    """Skill check -> dict with bar range (blue or red), fish x and catch %, or None.

    check_panel also confirms the panel's Q box is there (slower, but stops
    water or anything else blue behind the panel area being mistaken for it).
    """
    x0, x1 = TRACK_X
    band = img[TRACK_BAND[0]:TRACK_BAND[1], x0:x1]
    red = mask_red(band)
    bar_cols = (mask_blue(band) | red).sum(axis=0) >= 3
    bar = _runs(bar_cols)
    if bar is None or not (25 <= bar[1] - bar[0] <= 260):
        return None
    if check_panel and ncc_score(img, QBOX_SEARCH, QBOX_TPL) < 0.6:
        return None
    bar = (bar[0] + x0, bar[1] + x0)
    om = mask_orange(band)
    fish = None
    if om.sum() >= 25:
        xs = np.nonzero(om)[1]
        fish = float(np.median(xs)) + x0

    prog = None
    pb = mask_orange(img[PROGRESS_BAND[0]:PROGRESS_BAND[1]]).sum(axis=0) >= 3
    pr = np.nonzero(pb)[0]
    if len(pr):
        prog = pr.max()  # rightmost filled pixel (relative), shown in logs only
    is_red = red[:, bar[0] - x0:bar[1] - x0 + 1].mean() > 0.3
    return {"bar": bar, "fish": fish, "progress_px": prog, "red": bool(is_red)}


def ncc_score(img, search, tpl):
    """Best match (0..1) of a small grey picture inside a search area."""
    y0, y1, x0, x1 = search
    gray = img[y0:y1, x0:x1] @ np.array([0.299, 0.587, 0.114], np.float32)
    th, tw = tpl.shape
    if gray.shape[0] < th or gray.shape[1] < tw:
        return 0.0
    win = np.lib.stride_tricks.sliding_window_view(gray, (th, tw))
    t = tpl - tpl.mean()
    tn = np.sqrt((t * t).sum())
    wm = win.mean(axis=(2, 3), keepdims=True)
    w = win - wm
    num = np.einsum("ijkl,kl->ij", w, t)
    den = np.sqrt((w * w).sum(axis=(2, 3))) * tn + 1e-6
    return float((num / den).max())


def keep_score(img):
    """How much the screen looks like the 'E Keep' prompt (0..1)."""
    return ncc_score(img, KEEP_SEARCH, KEEP_TPL)


SCANCODES = {"1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
             "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
             "q": 0x10, "e": 0x12, "r": 0x13}

if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    PUL = ctypes.POINTER(ctypes.c_ulong)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", PUL)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

    class _INPUTunion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTunion)]

    def _send(scan, up):
        flags = 0x0008 | (0x0002 if up else 0)  # SCANCODE | KEYUP
        inp = INPUT(type=1)
        inp.u.ki = KEYBDINPUT(0, scan, flags, 0, None)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
else:
    def _send(scan, up):
        pass


class Keys:
    def __init__(self):
        self.down = set()

    def press(self, k):
        if k not in self.down:
            _send(SCANCODES[k], False)
            self.down.add(k)

    def release(self, k):
        if k in self.down:
            _send(SCANCODES[k], True)
            self.down.discard(k)

    def tap(self, k, hold=0.06):
        self.press(k)
        time.sleep(hold)
        self.release(k)

    def release_all(self):
        for k in list(self.down):
            self.release(k)


def _resize(a, h, w):
    """Smoothly resize an H x W x 3 image to h x w."""
    def axis(n_in, n_out):
        pos = (np.arange(n_out) + 0.5) * n_in / n_out - 0.5
        pos = np.clip(pos, 0, n_in - 1)
        lo = np.floor(pos).astype(int)
        hi = np.minimum(lo + 1, n_in - 1)
        return lo, hi, (pos - lo).astype(np.float32)
    y0, y1, wy = axis(a.shape[0], h)
    x0, x1, wx = axis(a.shape[1], w)
    a = a[y0] * (1 - wy)[:, None, None] + a[y1] * wy[:, None, None]
    return a[:, x0] * (1 - wx)[None, :, None] + a[:, x1] * wx[None, :, None]


class Screen:
    """Reads the fishing area for any resolution, on whichever monitor the
    Settings tab's Monitor picker selects.

    Everything is worked out on a 1920x1080 layout. On a bigger screen the
    fishing panels sit in the middle, near the bottom, and are either scaled
    up with the screen height or stay the same pixel size, depending on the
    server's UI. Both are tried until one is found, then that one is kept.
    """
    BW, BH = REGION[2] - REGION[0], REGION[3] - REGION[1]

    def __init__(self, sct, mon=None):
        self.sct = sct
        # mon is an mss-style dict {"left","top","width","height"} for the
        # chosen monitor (see ui.get_monitors()); fall back to the primary
        # monitor if nothing was supplied.
        if not mon:
            mon = sct.monitors[1]
        self._mon = mon
        self.W, self.H = mon["width"], mon["height"]
        self.ox, self.oy = mon["left"], mon["top"]
        cx_off = REGION[0] - 960      # panel offset from screen centre at 1080p
        s = self.H / 1080.0
        self.modes = [("scaled", self._box(cx_off * s, REGION[1] * s, self.BW * s, self.BH * s))]
        if abs(s - 1) > 0.01:
            self.modes.append(("same size", self._box(cx_off, self.H - (1080 - REGION[1]),
                                                       self.BW, self.BH)))
        self.i = 0
        self.locked = len(self.modes) == 1

    def _box(self, dx, top, w, h):
        return {"left": int(round(self.ox + self.W / 2.0 + dx)), "top": int(round(self.oy + top)),
                "width": int(round(w)), "height": int(round(h))}

    @property
    def resolution(self):
        return "%dx%d" % (self.W, self.H)

    def grab(self):
        a = np.asarray(self.sct.grab(self.modes[self.i][1]))  # BGRA
        a = a[..., 2::-1].astype(np.float32)                   # -> RGB
        if a.shape[0] != self.BH or a.shape[1] != self.BW:
            a = _resize(a, self.BH, self.BW)
        return a

    def miss(self):
        """Nothing found on this frame: try the other layout next time."""
        if not self.locked:
            self.i = (self.i + 1) % len(self.modes)

    def hit(self):
        """Something found: stick with this layout. Returns its name the first time."""
        if self.locked:
            return None
        self.locked = True
        return self.modes[self.i][0]


class Stop(Exception):
    pass


class Fisher:
    def __init__(self, out, monitor_provider=None):
        self.out = out                  # queue to the window
        self.keys = Keys()
        self.running = threading.Event()
        self.watch_only = False
        self.quit = False
        self.caught = 0
        self.lost = 0
        self.screen = None
        # Returns an mss-style monitor dict {"left","top","width","height"}
        # for whichever screen the Settings tab's Monitor picker has
        # selected — lets this macro target a non-primary monitor instead
        # of always assuming the game is on monitor 1.
        self.monitor_provider = monitor_provider or (lambda: None)

    # --- talking to the window ----------------------------------------------

    def log(self, msg):
        self.out.put(("log", time.strftime("%H:%M:%S ") + msg))

    def status(self, text, colour="#4cc38a"):
        self.out.put(("status", text, colour))

    def counts(self):
        self.out.put(("counts", self.caught, self.lost))

    def reset_screen(self):
        """Called when the Monitor picker changes, so the next frame grab
        rebuilds Screen() against the newly chosen monitor instead of
        staying locked to whichever one was active when the engine
        thread first started."""
        self.screen = None

    # --- helpers -----------------------------------------------------------

    def frame(self):
        if self.quit or not self.running.is_set() or self.watch_only:
            raise Stop
        return self.screen.grab()

    def wait_for(self, finder, timeout, stable=2):
        end, hits = time.time() + timeout, 0
        while time.time() < end:
            res = finder(self.frame())
            if res:
                hits += 1
                if hits >= stable:
                    self.found()
                    return res
            else:
                hits = 0
                self.screen.miss()
            time.sleep(0.01)
        return None

    def found(self):
        mode = self.screen.hit()
        if mode:
            self.log("Found the fishing UI (%s, %s)" % (self.screen.resolution, mode))

    # --- stages ------------------------------------------------------------

    def cast(self):
        self.status("Casting...")
        cast = None
        for attempt in range(3):
            self.keys.tap(S["rod_key"])
            cast = self.wait_for(find_cast, 2.5)
            if cast:
                break
            if find_skill(self.frame()):
                return True
            self.log("Cast bar didn't show, pressing %s again" % S["rod_key"])
            time.sleep(0.8)
        if not cast:
            self.log("Couldn't get the cast bar up - is the rod on slot %s?" % S["rod_key"])
            return False

        gl, gr = cast["green"]
        target = (gl + gr) / 2.0
        r0, r1 = cast["rows"]
        x_end = cast["yellow"][1] + 25
        base = self.frame()[r0:r1, :x_end].copy()

        self.keys.press("e")
        t0 = time.time()
        last_front, last_t, speed = None, None, 0.0
        try:
            while True:
                img = self.frame()
                now = time.time()
                diff = np.abs(img[r0:r1, :x_end] - base).sum(axis=2)
                changed = np.nonzero((diff > 45).sum(axis=0) >= 2)[0]
                if len(changed):
                    front = float(changed.max())
                    if last_front is not None and now > last_t:
                        inst = (front - last_front) / (now - last_t)
                        speed = inst if speed == 0 else 0.6 * speed + 0.4 * inst
                    last_front, last_t = front, now
                    if speed > 0 and front + speed * S["cast_early"] >= target:
                        break
                    if front > gr + 3 and speed > 0:
                        self.log("Cast went past green")
                        break
                if now - t0 > 6:
                    self.log("Couldn't see the cast charging, let go")
                    break
        finally:
            self.keys.release("e")
        if last_front is not None:
            spot = "green" if gl <= last_front <= gr + 3 else "early" if last_front < gl else "late"
            self.log("Cast released (%s)" % spot)
        return True

    def wait_bite(self):
        self.status("Waiting for a bite...", "#e0b34a")
        return self.wait_for(find_skill, S["bite_timeout"], stable=2) is not None

    def skill_check(self):
        self.status("Reeling it in!", "#4aa3e0")
        self.log("Fish hooked")
        fish_v = bar_v = 0.0
        last = None
        moving = 0
        gone_since = None
        said_red = False
        try:
            while True:
                img = self.frame()
                now = time.time()
                s = find_skill(img, check_panel=False)
                if not s:
                    gone_since = gone_since or now
                    self.keys.release_all()
                    moving = 0
                    if now - gone_since > 0.5:
                        break
                    continue
                gone_since = None
                if s["red"] and not said_red:
                    said_red = True
                    self.status("Legendary fish! Reeling...", "#e05a5a")
                    self.log("Red bar - legendary fish")
                bl, br = s["bar"]
                bar_c = (bl + br) / 2.0
                half = (br - bl) / 2.0
                fish = s["fish"] if s["fish"] is not None else (last[1] if last else bar_c)
                if last:
                    dt = max(now - last[0], 1e-3)
                    fish_v = 0.5 * fish_v + 0.5 * (fish - last[1]) / dt
                    bar_v = 0.5 * bar_v + 0.5 * (bar_c - last[2]) / dt
                last = (now, fish, bar_c)

                err = (fish + fish_v * S["fish_lead"]) - (bar_c + bar_v * S["bar_brake"])
                dead = half * S["deadzone"]
                if moving == 0:
                    want = 1 if err > dead else -1 if err < -dead else 0
                else:
                    want = moving if err * moving > dead * 0.3 else 0
                    if err * moving < -dead:
                        want = -moving
                if want != moving:
                    self.keys.release_all()
                    if want == 1:
                        self.keys.press("e")
                    elif want == -1:
                        self.keys.press("q")
                    moving = want
        finally:
            self.keys.release_all()

    def keep(self):
        end = time.time() + 4
        while time.time() < end:
            if keep_score(self.frame()) > 0.7:
                time.sleep(0.15)
                self.keys.tap("e")
                self.caught += 1
                self.log("Caught one! Kept it")
                self.counts()
                return True
            time.sleep(0.03)
        self.lost += 1
        self.log("Fish got away")
        self.counts()
        return False

    # --- loops -------------------------------------------------------------

    def watch(self):
        """Test mode: report what it sees, press nothing."""
        img = self.screen.grab()
        if img.max() < 8:
            self.status("Screen looks black - use Windowed Borderless", "#e05a5a")
            return
        c, s = find_cast(img), find_skill(img)
        k = keep_score(img) > 0.7
        if c or s or k:
            self.found()
        else:
            self.screen.miss()
        if c:
            self.status("I can see: the CAST bar", "#c38ae0")
        elif s:
            self.status("I can see: the SKILL CHECK", "#c38ae0")
        elif k:
            self.status("I can see: the KEEP prompt", "#c38ae0")
        else:
            self.status("I can see: nothing fishing-related", "#8a93a6")

    def thread_main(self):
        self._sct = None
        while not self.quit:
            if not self.running.is_set() and self.screen is None:
                time.sleep(0.05)
                continue
            if self.screen is None:
                # Built lazily on first Start (not at thread spawn) so a
                # Monitor change in Settings before pressing Start is
                # picked up instead of locking in monitor 1 forever.
                try:
                    import mss
                    if self._sct is None:
                        self._sct = mss.mss()
                    self.screen = Screen(self._sct, self.monitor_provider())
                    self.log("Screen: %s" % self.screen.resolution)
                except Exception as e:
                    self.out.put(("fatal", "Couldn't start screen reading:\n%s" % e))
                    self.running.clear()
                    time.sleep(0.5)
                    continue
            if not self.running.is_set():
                time.sleep(0.05)
                continue
            if self.watch_only:
                self.watch()
                time.sleep(0.25)
                continue
            try:
                if not self.cast():
                    time.sleep(2)
                    continue
                if not self.wait_bite():
                    self.log("No bite, casting again")
                    continue
                self.skill_check()
                self.keep()
                self.status("Next cast soon...")
                time.sleep(S["pause_between"])
            except Stop:
                self.keys.release_all()
            except Exception:
                self.keys.release_all()
                self.log("Error: " + traceback.format_exc().strip().splitlines()[-1])
                time.sleep(1)


# ═══════════════════════════════════════════════════════════════════════════════
#  WINDOW — black/white chrome matching the rest of the Suite
# ═══════════════════════════════════════════════════════════════════════════════
class FishingMacro(ui.Draggable):
    def __init__(self):
        self.q = queue.Queue()
        self._monitors = ui.get_monitors()
        self.f = Fisher(self.q, monitor_provider=self._get_monitor)
        self._start_ts = None
        self._run_seconds = 0.0
        self._hotkeys = ui.HotkeyWatcher()
        self._theme_mtime = ui.theme_mtime()
        self._cur_tab = 0

        self.root = tk.Tk()
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(False, False)
        self._center()
        ui.frameless(self.root, WIN_W, WIN_H, radius=20)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        threading.Thread(target=self.f.thread_main, daemon=True).start()
        self._tick()

    def _center(self):
        self.root.update_idletasks()
        sw, sh = ui.primary_screen_size(self.root)
        self.root.geometry(f"{WIN_W}x{WIN_H}+{(sw-WIN_W)//2}+{(sh-WIN_H)//2}")

    def _get_monitor(self):
        label = self.v_monitor.get()
        for name, left, top, w, h in self._monitors:
            if name == label:
                return {"left": left, "top": top, "width": w, "height": h}
        return None

    # ── layout ───────────────────────────────────────────────────────────────
    def _build(self):
        bgcv = tk.Canvas(self.root, width=WIN_W, height=WIN_H, highlightthickness=0, bg=ui.BG)
        bgcv.place(x=0, y=0)
        self.badge = ui.macro_header(self, WIN_W, "🎣", "FISHING MACRO", ui.CYAN)
        tk.Frame(self.root, bg=ui.CYAN, height=2).place(x=0, y=ui.HEADER_H, width=WIN_W)

        sb = tk.Frame(self.root, bg=ui.PANEL2, highlightbackground=ui.BORDER, highlightthickness=1)
        sb.place(x=0, y=ui.HEADER_H+2, width=ui.SIDEBAR_W, height=WIN_H-ui.HEADER_H-2)
        self._stabs = []
        for i, (ico, name) in enumerate([("🎣", "Macro"), ("📊", "Stats"), ("⚙", "Settings")]):
            btn = ui.SideIconBtn(sb, ico, name, active_color=ui.CYAN)
            btn.frame.pack(fill="x")
            btn.bind("<Button-1>", lambda e, idx=i: self._tab(idx))
            self._stabs.append(btn)

        cx = ui.SIDEBAR_W+1; cw = WIN_W-cx; ch = WIN_H-ui.HEADER_H-2
        self.cont = tk.Frame(self.root, bg=ui.PANEL)
        self.cont.place(x=cx, y=ui.HEADER_H+2, width=cw, height=ch)
        v0 = self._mk_macro_view(cw, ch)
        v1 = self._mk_stats_view(cw, ch)
        v2 = self._mk_settings_view(cw, ch)
        self._views = [v0, v1, v2]
        self._tab(0)

    def _mk_macro_view(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h); f.pack_propagate(False)
        body = tk.Frame(f, bg=ui.PANEL); body.pack(fill="both", expand=True, padx=20, pady=14)
        tk.Label(body, text="🎣", bg=ui.PANEL, fg=ui.CYAN, font=("Segoe UI Emoji", 32)).pack(pady=(4,2))
        tk.Label(body, text="Fishing Auto-Macro", bg=ui.PANEL, fg=ui.FG, font=("Segoe UI Black", 14)).pack()
        tk.Label(body, text="Casts, reads the skill-check, and keeps the catch",
                 bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI", 9)).pack(pady=(2,10))
        self._status_lbl = tk.Label(body, text="Idle", bg=ui.PANEL, fg=ui.MUTED,
                                     font=("Segoe UI Semibold", 9))
        self._status_lbl.pack(pady=(0,8))
        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x")
        tiles = tk.Frame(body, bg=ui.PANEL); tiles.pack(fill="x", pady=10)
        self._t_caught = ui.StatTile(tiles, "CAUGHT", "0", accent=ui.GREEN)
        self._t_caught.frame.pack(side="left", expand=True, fill="both", padx=(0,4))
        self._t_lost = ui.StatTile(tiles, "GOT AWAY", "0", accent=ui.RED)
        self._t_lost.frame.pack(side="left", expand=True, fill="both", padx=(4,0))
        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(0,10))
        self._toggle = ui.ToggleButton(body, self._on_toggle, start_bg=ui.CYAN,
                                        start_label="▶  START FISHING", stop_label="■  STOP FISHING",
                                        stop_bg=ui.RED)
        self._toggle.btn.pack(fill="x", ipady=4); self._toggle.btn.configure(fg=ui.BG)
        ui.hotkey_row(body, [("F5","Start"), ("F6","Stop"), ("F10","Close")],
                      bg=ui.PANEL).pack(fill="x", pady=(8,0))
        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(8,4))
        self._log, sb2 = ui.log_box(body, height=5)
        sb2.pack(side="right", fill="y"); self._log.pack(side="left", fill="both", expand=True)
        return f

    def _mk_stats_view(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        body = tk.Frame(f, bg=ui.PANEL); body.pack(fill="both", expand=True, padx=20, pady=20)
        tk.Label(body, text="SESSION STATS", bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(4,12))
        def row(lbl):
            r = tk.Frame(body, bg=ui.PANEL); r.pack(fill="x", pady=4)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI",9)).pack(side="left")
            v = tk.StringVar(value="0")
            tk.Label(r, textvariable=v, bg=ui.PANEL, fg=ui.FG, font=("Segoe UI Semibold",9)).pack(side="right")
            return v
        self._sv_caught = row("Fish caught")
        self._sv_lost   = row("Got away")
        self._sv_uptime = row("Uptime")
        return f

    def _mk_settings_view(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        body = tk.Frame(f, bg=ui.PANEL); body.pack(fill="both", expand=True, padx=20, pady=16)

        ui.section_label(body, "Screen", bg=ui.PANEL)
        mon_row = tk.Frame(body, bg=ui.PANEL); mon_row.pack(fill="x", pady=5)
        tk.Label(mon_row, text="Monitor", bg=ui.PANEL, fg=ui.FG2, font=("Segoe UI",9)).pack(side="left")
        self.v_monitor = tk.StringVar(value=self._monitors[0][0])
        self.v_monitor.trace_add("write", lambda *a: self.f.reset_screen())
        ui.dropdown(mon_row, self.v_monitor, [m[0] for m in self._monitors], width=16).pack(side="right")

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=6)
        ui.section_label(body, "Rod", bg=ui.PANEL)
        rod_row = tk.Frame(body, bg=ui.PANEL); rod_row.pack(fill="x", pady=5)
        tk.Label(rod_row, text="Rod slot key", bg=ui.PANEL, fg=ui.FG2, font=("Segoe UI",9)).pack(side="left")
        self.v_rod = tk.StringVar(value=S["rod_key"])
        self.v_rod.trace_add("write", lambda *a: S.__setitem__("rod_key", self.v_rod.get()))
        ui.dropdown(rod_row, self.v_rod, list("1234567890"), width=4).pack(side="right")

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=6)
        ui.section_label(body, "Behaviour", bg=ui.PANEL)
        self.v_watch = tk.BooleanVar(value=False)
        wr = tk.Frame(body, bg=ui.PANEL); wr.pack(fill="x", pady=5)
        tk.Label(wr, text="Test mode (watch only, no key presses)", bg=ui.PANEL, fg=ui.FG2,
                 font=("Segoe UI",9)).pack(side="left")
        tk.Checkbutton(wr, variable=self.v_watch, command=lambda: setattr(self.f, "watch_only", self.v_watch.get()),
                       bg=ui.PANEL, selectcolor=ui.PANEL2, activebackground=ui.PANEL,
                       relief="flat", bd=0).pack(side="right")

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=6)
        ui.section_label(body, "Timing", bg=ui.PANEL)
        def rowf(lbl, key, default, lo, hi, step):
            r = tk.Frame(body, bg=ui.PANEL); r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.FG2, font=("Segoe UI",9)).pack(side="left")
            v = tk.DoubleVar(value=default)
            v.trace_add("write", lambda *a, k=key, vv=v: S.__setitem__(k, vv.get()))
            ui.spinbox(r, v, lo, hi, step=step, width=7).pack(side="right")
            return v
        self.v_cast_early = rowf("Cast timing (early release)", "cast_early", S["cast_early"], 0.0, 0.15, 0.005)
        self.v_deadzone   = rowf("Reel tightness", "deadzone", S["deadzone"], 0.05, 0.5, 0.01)
        self.v_bar_brake  = rowf("Bar slide fix", "bar_brake", S["bar_brake"], 0.0, 0.2, 0.01)
        self.v_bite_to    = rowf("Bite timeout (s)", "bite_timeout", S["bite_timeout"], 10, 300, 5)
        self.v_pause      = rowf("Pause between casts (s)", "pause_between", S["pause_between"], 0.2, 5.0, 0.1)
        return f

    def _tab(self, idx):
        self._cur_tab = idx
        for i, btn in enumerate(self._stabs): btn.set_active(i == idx)
        for i, v in enumerate(self._views):
            if i == idx: v.place(x=0, y=0, relwidth=1, relheight=1); v.lift()
            else: v.place_forget()

    # ── live theme reload ───────────────────────────────────────────────────
    def _rebuild_theme(self):
        """A colour change in the Hub writes the shared theme file; pick it
        up here (reload ui so widget-default colours refresh too, not just
        explicit ui.X lookups) and rebuild this window in place, without
        touching the running engine thread or its counters."""
        ui.reload_theme_module()
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=ui.BG)
        self._build()
        self._tab(self._cur_tab)
        if self.f.running.is_set():
            self.badge.set("Running", ui.GREEN)
            self._toggle.btn.configure(text="■  STOP FISHING", bg=ui.RED, fg="white")
            self._toggle._running = True

    # ── start/stop ───────────────────────────────────────────────────────────
    def _on_toggle(self, running):
        if running: self._start()
        else: self._stop()

    def _start(self):
        if self.f.running.is_set(): return
        self._start_ts = time.time()
        self.f.running.set()
        self.badge.set("Running", ui.GREEN)
        self._toggle.btn.configure(text="■  STOP FISHING", bg=ui.RED, fg="white")
        ui.append_log(self._log, "▶ Fishing started")

    def _stop(self):
        if not self.f.running.is_set(): return
        self.f.running.clear()
        if self._start_ts:
            self._run_seconds += time.time() - self._start_ts
            self._start_ts = None
        self.badge.set("Stopped", ui.MUTED)
        self._toggle.force_stop()
        self._status_lbl.configure(text="Idle")
        ui.append_log(self._log, "■ Fishing stopped")

    # ── queue drain / UI tick ───────────────────────────────────────────────
    def _tick(self):
        # Global hotkeys — polled key state, not Tk <bind>, so these fire
        # while the game window has focus, not just the macro window.
        if self._hotkeys.pressed(ui.VK_F5): self._start()
        if self._hotkeys.pressed(ui.VK_F6): self._stop()
        if self._hotkeys.pressed(ui.VK_F10): self._quit(); return
        m = ui.theme_mtime()
        if m != self._theme_mtime:
            self._theme_mtime = m
            self._rebuild_theme()
            self.root.after(50, self._tick)
            return
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    ui.append_log(self._log, msg[1])
                elif kind == "status":
                    self._status_lbl.configure(text=msg[1])
                elif kind == "counts":
                    self._t_caught.set(msg[1]); self._t_lost.set(msg[2])
                    self._sv_caught.set(str(msg[1])); self._sv_lost.set(str(msg[2]))
                elif kind == "fatal":
                    ui.append_log(self._log, f"  ⚠ {msg[1]}")
                    tkinter.messagebox.showerror("XZX Fishing", msg[1])
        except queue.Empty:
            pass
        secs = self._run_seconds + (time.time() - self._start_ts if self._start_ts else 0)
        self._sv_uptime.set(f"{int(secs//3600):02d}:{int((secs%3600)//60):02d}:{int(secs%60):02d}")
        self.root.after(50, self._tick)

    def _quit(self):
        self.f.quit = True
        self.f.running.clear()
        self.f.keys.release_all()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FishingMacro().run()
