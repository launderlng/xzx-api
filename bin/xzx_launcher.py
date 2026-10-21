"""
XZX Launcher — Login + Hub
Sign in with Discord (or paste a key) to unlock whichever macros that
account has active keys for. No OS title bar. Black/white theme.

ACCESS CONTROL (see the top-level safety note in the repo for what this
deliberately does NOT do — no file deletion, no drive scanning):
  - HWID fingerprint locked to machine
  - Server-side validation on every activation (no cached bypass)
  - Env var session token required by all macro processes, checked once
    at each macro's startup — not polled continuously
  - Session token rotates each launcher run
  - Anti-debug is disabled (see note below) — it was a false-positive
    magnet on machines with antivirus/EDR software, not a real gate
"""
import tkinter as tk
from tkinter import colorchooser
import threading, subprocess, os, sys, json, hashlib, time, webbrowser, random, hmac
import urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xzx_ui as ui

WIN_W, WIN_H  = 440, 610
HUB_W, HUB_H  = 820, 560
LICENSE_FILE   = Path(__file__).parent / "launcher_license.json"
DISCORD_URL    = "https://discord.gg/xzxx"
API_VERIFY     = "https://api.xzxsuite.com/verify"
API_LINK_CHECK = "https://api.xzxsuite.com/linked"
API_SESSION    = "https://api.xzxsuite.com/session"
SECRET_SALT    = b"xzx_suite_2025_salt_noclone"

# ── license key activation — the bot's OWN built-in key system ─────────────────
# This is the real, already-working purchase flow: Stripe checkout issues a key
# through the bot, and the bot itself serves /api/activate (see cogs/licenses.py
# on the bot). Entering that key here is how the launcher unlocks, independent
# of the Discord-OAuth path above.
BOT_BASE      = os.getenv("XZX_BOT_BASE",
                          "https://verification-bot-production-2ea4.up.railway.app")
API_ACTIVATE  = f"{BOT_BASE}/api/activate"
KEY_PRODUCT   = "any"   # discover whichever macro a key is actually for, rather
                         # than guessing one — the server tells us in the response

# ── anti-debug ─────────────────────────────────────────────────────────────────
# NOTE: disabled — IsDebuggerPresent() throws false positives on machines with
# antivirus/EDR software (Defender, etc.), silently killing the app for real
# customers with no window and no error. Not worth the breakage.
def _check_debugger():
    pass

# ── session token (changes every run, passed to child procs via env) ───────────
def _make_session_token(discord_id: str, hwid: str) -> str:
    ts  = str(int(time.time() // 300))   # 5-min window
    raw = f"{discord_id}:{hwid}:{ts}".encode()
    return hmac.new(SECRET_SALT, raw, hashlib.sha256).hexdigest()[:24]

SESSION_TOKEN: str = ""   # set after successful login

# ── HWID ───────────────────────────────────────────────────────────────────────
def get_hwid() -> str:
    parts = []
    # 1. Windows MachineGuid (most stable)
    try:
        import winreg
        k    = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SOFTWARE\Microsoft\Cryptography")
        parts.append(winreg.QueryValueEx(k, "MachineGuid")[0])
    except Exception:
        pass
    # 2. MAC address fallback
    try:
        import uuid
        parts.append(str(uuid.getnode()))
    except Exception:
        pass
    # 3. CPU ID via wmic (Windows)
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ["wmic","cpu","get","ProcessorId"],
            stderr=_sp.DEVNULL, timeout=4).decode()
        pid = [l.strip() for l in out.splitlines()
               if l.strip() and "ProcessorId" not in l]
        if pid:
            parts.append(pid[0])
    except Exception:
        pass
    combined = "|".join(parts) or "fallback"
    return hashlib.sha256(combined.encode()).hexdigest()[:32]

HWID = get_hwid()

# ── license helpers ─────────────────────────────────────────────────────────────
def _sign(data: dict) -> str:
    payload = json.dumps(data, sort_keys=True).encode()
    return hmac.new(SECRET_SALT, payload, hashlib.sha256).hexdigest()

def load_license():
    """Load cached license — ONLY used as offline fallback, never to skip server check."""
    try:
        raw  = json.loads(LICENSE_FILE.read_text())
        data = {k: v for k, v in raw.items() if k != "_sig"}
        if raw.get("_sig") != _sign(data):
            LICENSE_FILE.unlink(missing_ok=True)   # tampered — wipe it
            return None
        if data.get("hwid") != HWID:
            return None   # different machine — reject
        return data
    except Exception:
        return None

def save_license(d: dict):
    data = {**d, "hwid": HWID}
    data["_sig"] = _sign({k: v for k, v in data.items() if k != "_sig"})
    LICENSE_FILE.write_text(json.dumps(data))

def wipe_license():
    try: LICENSE_FILE.unlink(missing_ok=True)
    except Exception: pass

# ── API helpers ─────────────────────────────────────────────────────────────────
def api_post(url, payload: dict, timeout=8):
    import urllib.request, urllib.parse
    # always include HWID + timestamp to prevent replay
    payload = {**payload, "hwid": HWID, "ts": int(time.time())}
    data = urllib.parse.urlencode(payload).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-XZX-HWID",  HWID)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def validate_discord_id(discord_id: str):
    """
    Always hits the server first. Falls back to cache ONLY on network error.
    Server checks: valid purchase, not banned, HWID matches or is being bound.
    """
    try:
        resp = api_post(API_VERIFY, {"discord_id": discord_id})
        if resp.get("banned"):
            return None, "banned"
        if resp.get("valid"):
            return {
                "discord_id": discord_id,
                "username":   resp.get("username", discord_id),
                "tag":        resp.get("tag", ""),
                "linked":     resp.get("linked", False),
                "macros":     resp.get("macros", []),
            }, "ok"
        return None, resp.get("reason", "invalid")
    except OSError:
        # network unavailable — allow cached license (offline mode)
        cached = load_license()
        if cached and cached.get("discord_id") == discord_id:
            return cached, "offline"
        return None, "no_network"
    except Exception:
        return None, "error"

def check_linked(discord_id: str) -> bool:
    try:
        resp = api_post(API_LINK_CHECK, {"discord_id": discord_id})
        return bool(resp.get("linked"))
    except Exception:
        cached = load_license()
        return bool(cached and cached.get("linked"))

def heartbeat(discord_id: str, session_tok: str) -> bool:
    """Returns False if server has revoked this session."""
    try:
        resp = api_post(API_SESSION,
                        {"discord_id": discord_id,
                         "session_token": session_tok})
        return bool(resp.get("valid"))
    except Exception:
        return True   # network down → assume still valid (fail-open for UX)

def key_still_valid(code: str) -> bool:
    """
    Periodic re-check for a key-based login, against the bot's own
    /api/check (quiet — doesn't count as a new login). Returns False
    only when the bot explicitly says the key was REVOKED.
    """
    import urllib.request, urllib.parse
    try:
        payload = urllib.parse.urlencode({
            "code": code, "product": KEY_PRODUCT, "hwid": HWID,
        }).encode()
        req = urllib.request.Request(f"{BOT_BASE}/api/check",
                                     data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode(errors="replace").strip()
        return not text.startswith("REVOKED")
    except Exception:
        return True   # network down → fail-open

# ── macros ──────────────────────────────────────────────────────────────────────
MACROS = [
    {"id":"fishing",  "icon":"🎣", "name":"Fishing",
     "color": ui.CYAN,   "file":"xzx_fishing.py",  "exe":"XZX Fishing.exe",
     "desc":"Auto-detects fish bite and casts/reels for you in FiveM.",
     "keys":["F5 Start","F6 Stop","F10 Close"]},
    {"id":"metal",    "icon":"🔍", "name":"Metal Detector",
     "color": ui.ACCENT, "file":"xzx_metal.py",    "exe":"XZX Metal.exe",
     "desc":"GDI pixel scan — auto skill-check on metal detector minigame.",
     "keys":["F7 Start","F8 Stop","F10 Close"]},
    {"id":"altclick", "icon":"🖱️", "name":"Alt Click",
     "color": ui.GREEN,  "file":"xzx_altclick.py", "exe":"XZX AltClick.exe",
     "desc":"Hold-Alt auto-click macro with configurable CPS.",
     "keys":["F9 Toggle","F10 Close"]},
]
MACRO_IDS = [m["id"] for m in MACROS]

# ── drag mixin ──────────────────────────────────────────────────────────────────
class Draggable:
    def _make_draggable(self, widget):
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>",     self._drag_motion)
    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()
    def _drag_motion(self, e):
        self.root.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════════
class LoginWindow(Draggable):
    RADIUS = 28  # whole-window corner radius

    def __init__(self):
        self._discord_id  = ""
        self._discord_tag = ""
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=ui.BG)
        self._center()
        self.root.after(60, lambda: ui.round_window(
            self.root, WIN_W, WIN_H, self.RADIUS))
        # subtle glass-like translucency — safe now that the window is
        # genuinely clipped to rounded corners (this used to cause ghosting
        # when combined with a square window + a separately-rounded card)
        try: self.root.attributes("-alpha", 0.94)
        except Exception: pass
        try: self.root.attributes("-topmost", True)
        except Exception: pass
        self._build()

    def _center(self):
        self.root.update_idletasks()
        sw, sh = ui.primary_screen_size(self.root)
        self.root.geometry(f"{WIN_W}x{WIN_H}+{(sw-WIN_W)//2}+{(sh-WIN_H)//2}")

    def _build(self):
        # ── one continuous gradient surface — the whole rounded window ─────
        self.cv = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                            highlightthickness=0, bg=ui.BG)
        self.cv.place(x=0, y=0)
        self.root.after(10, self._paint_bg)

        # ── minimal title strip: just a drag handle + close, no label ──────
        bar = tk.Frame(self.root, bg=ui.BG, height=30)
        bar.place(x=0, y=0, width=WIN_W, height=30)
        self._make_draggable(bar)
        cb = tk.Label(bar, text="✕", bg=ui.BG, fg=ui.MUTED,
                      font=("Segoe UI", 12), cursor="hand2")
        cb.place(relx=1.0, x=-18, rely=0.5, anchor="e")
        cb.bind("<Button-1>", lambda e: self.root.destroy())
        cb.bind("<Enter>",    lambda e: cb.configure(fg=ui.RED))
        cb.bind("<Leave>",    lambda e: cb.configure(fg=ui.MUTED))

        # ── centred content wrapper ───────────────────────────────────────
        wrap = tk.Frame(self.root, bg=ui.BG)
        wrap.place(relx=0.5, rely=0.55, anchor="center")

        # ── logo with soft glow ─────────────────────────────────────────────
        lc = tk.Canvas(wrap, width=110, height=110,
                       bg=ui.BG, highlightthickness=0)
        lc.pack(pady=(0, 10))
        self.root.after(20, lambda: self._draw_logo(lc, 110, 110))

        # product name
        tk.Label(wrap, text="XZX LAUNCHER", bg=ui.BG, fg=ui.FG,
                 font=("Segoe UI Black", 21)).pack()
        tk.Label(wrap, text="MEMBERS AREA", bg=ui.BG, fg=ui.ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(pady=(3, 24))

        # ── single content panel — part of the one rounded window,        ─
        # ── no nested card / shadow, just a clean flat section ───────────
        CARD_W = WIN_W - 72
        card = ui.RoundFrame(wrap, bg=ui.PANEL, border=ui.BORDER, border_w=1, r=16)
        card.configure(width=CARD_W)
        card.pack()
        cbody = card.body
        tk.Frame(cbody, bg=ui.ACCENT, height=2).pack(fill="x")

        inn = tk.Frame(cbody, bg=ui.PANEL)
        inn.pack(fill="both", expand=True, padx=26, pady=26)

        # ── Discord icon ──────────────────────────────────────────────────
        tk.Label(inn, text="🎮", bg=ui.PANEL, fg="#5865F2",
                 font=("Segoe UI Emoji", 30)).pack(pady=(0, 12))

        tk.Label(inn, text="Sign in with Discord",
                 bg=ui.PANEL, fg=ui.FG,
                 font=("Segoe UI Black", 13)).pack()

        tk.Label(inn,
                 text="Your browser will open — sign in and\ncome right back here.",
                 bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI", 9), justify="center").pack(pady=(7, 20))

        # ── rounded Discord OAuth pill button ───────────────────────────────
        BTN_W = CARD_W - 52
        self.btn = ui.RoundButton(inn, "Login with Discord",
                                  self._start_oauth,
                                  w=BTN_W, h=44, r=12,
                                  bg="#5865F2", hover="#4752c4",
                                  font=("Segoe UI Black", 11),
                                  parent_bg=ui.PANEL)
        self.btn.pack()

        self.stat_var = tk.StringVar()
        self.stat_lbl = tk.Label(inn, textvariable=self.stat_var,
                                  bg=ui.PANEL, fg=ui.MUTED,
                                  font=("Segoe UI", 8))
        self.stat_lbl.pack(pady=(10, 0))

        # ── divider ──────────────────────────────────────────────────────
        drow = tk.Frame(inn, bg=ui.PANEL)
        drow.pack(fill="x", pady=(18, 14))
        tk.Frame(drow, bg=ui.BORDER, height=1).pack(side="left", fill="x",
                                                     expand=True)
        tk.Label(drow, text="  OR ENTER YOUR KEY  ", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI Semibold", 7)).pack(side="left")
        tk.Frame(drow, bg=ui.BORDER, height=1).pack(side="left", fill="x",
                                                     expand=True)

        # ── license key entry ───────────────────────────────────────────────
        self.key_var = tk.StringVar()
        key_entry = tk.Entry(inn, textvariable=self.key_var,
                             bg=ui.PANEL2, fg=ui.FG,
                             insertbackground=ui.ACCENT, justify="center",
                             font=("Consolas", 11), relief="flat",
                             highlightthickness=1, highlightbackground=ui.BORDER,
                             highlightcolor=ui.ACCENT)
        key_entry.pack(fill="x", ipady=8)
        key_entry.insert(0, "XZX-XXXX-XXXX-XXXX")
        key_entry.configure(fg=ui.MUTED)

        def _focus_in(e):
            if self.key_var.get() == "XZX-XXXX-XXXX-XXXX":
                key_entry.delete(0, "end")
                key_entry.configure(fg=ui.FG)
        def _focus_out(e):
            if not self.key_var.get().strip():
                key_entry.insert(0, "XZX-XXXX-XXXX-XXXX")
                key_entry.configure(fg=ui.MUTED)
        key_entry.bind("<FocusIn>", _focus_in)
        key_entry.bind("<FocusOut>", _focus_out)
        key_entry.bind("<Return>", lambda e: self._activate_key())
        self._key_entry = key_entry

        self.key_btn = ui.RoundButton(inn, "Activate Key",
                                      self._activate_key,
                                      w=BTN_W, h=40, r=11,
                                      bg=ui.ACCENT2, hover="#e2e2e2",
                                      font=("Segoe UI Black", 10),
                                      parent_bg=ui.PANEL)
        self.key_btn.pack(pady=(10, 0))

        tk.Label(inn, text="🔒  Validated server-side on every launch",
                 bg=ui.PANEL, fg=ui.BORDER,
                 font=("Segoe UI", 7)).pack(pady=(14, 0))
        card.sync_size()

        # ── footer — buy-access link, no clutter ────────────────────────────
        foot = tk.Frame(self.root, bg=ui.BG)
        foot.place(x=0, y=WIN_H - 32, width=WIN_W, height=32)
        fwrap = tk.Frame(foot, bg=ui.BG)
        fwrap.pack(expand=True)
        tk.Label(fwrap, text="No key? Buy access →",
                 bg=ui.BG, fg=ui.MUTED,
                 font=("Segoe UI", 8)).pack(side="left")
        dl = tk.Label(fwrap, text=" discord.gg/xzxx",
                      bg=ui.BG, fg=ui.ACCENT,
                      font=("Segoe UI Semibold", 8), cursor="hand2")
        dl.pack(side="left")
        dl.bind("<Button-1>", lambda e: webbrowser.open(DISCORD_URL))

        # particles
        self._pts = []
        self._init_pts()
        self._anim_pts()

        # if there's a cached session, show a hint but still require OAuth
        cached = load_license()
        if cached:
            tag = cached.get("username") or cached.get("discord_id", "")
            self._setstatus(f"Previous session: {tag} — re-authorise to continue", ui.MUTED)

    def _draw_logo(self, cv, w, h):
        # No shape, no glow rings, no second layer behind it — just the
        # wordmark sitting directly on the starfield canvas underneath it.
        cx, cy = w // 2, h // 2
        cv.create_text(cx, cy, text="XZX", fill=ui.FG,
                       font=("Segoe UI Black", 22))

    def _paint_bg(self):
        # one flat layer: pure black, nothing drawn under or behind it —
        # the star particles (started in __init__) are the only other thing
        # on this canvas, so there's never a second "panel" floating on top.
        self.cv.configure(bg=ui.BG)
        self.cv.create_rectangle(0, 30, WIN_W, 31, fill=ui.BORDER, outline="")
        self._draw_scanline()

    def _draw_scanline(self):
        """One orchestrated reveal moment: a signal sweep crosses the card
        once on open, tying back to what XZX actually sells (detection
        macros) instead of a generic fade-in."""
        line = self.cv.create_line(0, 34, WIN_W, 34, fill=ui.ACCENT, width=1)
        glow = self.cv.create_rectangle(0, 32, WIN_W, 36, fill=ui.ACCENT, outline="", stipple="gray12")

        def step(y=34):
            if y > WIN_H - 20:
                self.cv.delete(line); self.cv.delete(glow)
                return
            self.cv.coords(line, 0, y, WIN_W, y)
            self.cv.coords(glow, 0, y - 4, WIN_W, y + 4)
            self.root.after(6, lambda: step(y + 6))

        self.root.after(250, step)

    def _setstatus(self, t, c=ui.MUTED):
        self.stat_var.set(t); self.stat_lbl.configure(fg=c)

    # ── Discord OAuth flow ─────────────────────────────────────────────────────
    def _start_oauth(self):
        """Sign in with Discord via the bot's own linking system, then auto-activate
        whatever key (if any) is already tied to that Discord account — e.g. from a
        manually created order. No key needs to be pasted for this path."""
        _check_debugger()
        self._setstatus("Opening Discord — sign in and come back…", ui.MUTED)
        self.btn.configure(state="disabled")
        self.key_btn.configure(state="disabled")
        threading.Thread(target=self._discord_login_flow, daemon=True).start()

    def _discord_login_flow(self):
        import urllib.request, urllib.parse

        def post(url, data=None):
            req = urllib.request.Request(url, data=data,
                                         method="POST" if data is not None else "GET")
            if data is not None:
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read().decode(errors="replace").strip()

        # ── step 1: ask the bot for a Discord-link session ──────────────────
        try:
            resp = post(f"{BOT_BASE}/api/auth/new", data=b"")
            parts = resp.split("|", 2)
            if parts[0] == "OK" and len(parts) >= 3:
                session, auth_url = parts[1], parts[2]
                webbrowser.open(auth_url)
            elif parts[0] == "OFF":
                self.root.after(0, lambda: self._oauth_done(
                    False, "Discord linking isn't set up on the server."))
                return
            else:
                self.root.after(0, lambda: self._oauth_done(
                    False, "Couldn't start Discord link. Try again."))
                return
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._oauth_done(False, f"Server error: {e}"))
            return

        # ── step 2: poll until the browser sign-in completes ────────────────
        deadline = time.time() + 120
        linked = False
        while time.time() < deadline:
            try:
                status_resp = urllib.request.urlopen(
                    f"{BOT_BASE}/api/auth/status?s={urllib.parse.quote(session)}",
                    timeout=10).read().decode(errors="replace").strip()
            except Exception:
                status_resp = ""
            if status_resp.startswith("LINKED"):
                linked = True
                lparts = status_resp.split("|")
                self._discord_tag = lparts[1] if len(lparts) > 1 else ""
                self._discord_id  = lparts[2] if len(lparts) > 2 else ""
                break
            if status_resp.startswith("ERROR") or status_resp == "EXPIRED":
                reason = status_resp.split("|", 1)[-1] if "|" in status_resp else "Link expired"
                self.root.after(0, lambda m=reason: self._oauth_done(False, m))
                return
            time.sleep(2)
        if not linked:
            self.root.after(0, lambda: self._oauth_done(False, "Discord sign-in timed out. Try again."))
            return

        self.root.after(0, lambda: self._setstatus("Linked! Looking up your key…", ui.MUTED))

        # ── step 3: does this Discord account already own a key? ────────────
        try:
            mk = urllib.request.urlopen(
                f"{BOT_BASE}/api/mykeys?session={urllib.parse.quote(session)}",
                timeout=10).read().decode(errors="replace").strip()
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._oauth_done(False, f"Server error: {e}"))
            return

        if mk.startswith("OK|"):
            codes = [c.strip() for c in mk.split("|", 1)[1].split(",") if c.strip()]
            self.root.after(0, lambda: self._setstatus(
                f"Found {len(codes)} key(s) — activating…", ui.MUTED))
            self._activate_all(codes, session)
        elif mk.startswith("DENIED|NO LICENSE"):
            self.root.after(0, lambda: self._oauth_done(
                False, "No key found for this Discord account. Buy one or enter a key below."))
        else:
            reason = mk.split("|", 1)[-1] if "|" in mk else mk
            self.root.after(0, lambda m=reason: self._oauth_done(False, m))

    def _oauth_done(self, ok: bool, message: str = ""):
        self.btn.configure(state="normal")
        self.key_btn.configure(state="normal")
        if not ok:
            self._setstatus(f"❌  {message}", ui.RED)

    # ── license key activation (bot's own /api/activate) ────────────────────
    def _activate_key(self):
        code = self.key_var.get().strip().upper()
        if not code or code == "XZX-XXXX-XXXX-XXXX":
            self._setstatus("Enter the key from your purchase DM.", ui.RED)
            return
        self.key_btn.configure(state="disabled")
        self.btn.configure(state="disabled")
        self._setstatus("Checking key…", ui.MUTED)
        threading.Thread(target=self._do_activate, args=(code,), daemon=True).start()

    def _raw_activate(self, code: str, session: str = "") -> tuple:
        """POSTs /api/activate for one code and returns (ok, detail_or_macro).
        product is always "any" — the server tells us which macro the key is
        actually for ("all" for a key that works everywhere) instead of us
        having to guess one up front."""
        import urllib.request, urllib.parse
        try:
            payload = urllib.parse.urlencode({
                "code": code, "product": KEY_PRODUCT, "hwid": HWID, "session": session,
            }).encode()
            req = urllib.request.Request(API_ACTIVATE, data=payload, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=12) as r:
                text = r.read().decode(errors="replace").strip()
        except Exception as exc:
            return False, f"Server error: {exc}"
        parts = text.split("|")
        if parts[0] == "OK":
            macro = parts[2] if len(parts) > 2 else "all"
            return True, macro
        return False, (parts[1] if len(parts) > 1 else "Key not accepted.")

    def _fetch_key_info(self, code: str) -> str:
        """Plain-text length/expiry for a key (e.g. 'Lifetime', 'Expires in 12d'),
        for showing in the Hub. Best-effort — an empty string just means the
        Hub shows nothing instead of a number."""
        if not code:
            return ""
        try:
            resp = urllib.request.urlopen(
                f"{BOT_BASE}/api/keyinfo?code={urllib.parse.quote(code)}",
                timeout=8).read().decode(errors="replace").strip()
            parts = resp.split("|")
            if parts[0] == "OK" and len(parts) > 2:
                return parts[2]
        except Exception:
            pass
        return ""

    def _activate_all(self, codes: list, session: str):
        """Activates every key a linked Discord account owns, building up the
        set of macros it unlocks (a plain key unlocks one; an 'all macros' key
        unlocks every tab) — this is what makes the Hub show only what was
        actually bought instead of guessing a single macro."""
        owned = {}
        last_error = "No key found for this Discord account. Buy one or enter a key below."
        any_ok = False
        for code in codes:
            ok, result = self._raw_activate(code, session)
            if ok:
                any_ok = True
                if result == "all":
                    for mid in MACRO_IDS:
                        owned[mid] = code
                else:
                    owned[result] = code
            else:
                last_error = result
        if any_ok:
            codes = sorted(set(owned.values()))
            key_length = self._fetch_key_info(codes[0]) if codes else ""
            self.root.after(0, lambda: self._login_finish(owned, key_length))
        else:
            self.root.after(0, lambda: self._oauth_done(False, last_error))

    def _login_finish(self, owned_macros: dict, key_length: str = ""):
        self.key_btn.configure(state="normal")
        self.btn.configure(state="normal")
        codes = sorted(set(owned_macros.values()))
        data = {
            "discord_id":   self._discord_id,
            "username":     self._discord_tag or self._discord_id,
            "tag":          self._discord_tag or self._discord_id,
            "linked":       True,
            "owned_macros": owned_macros,
            "license_key":  codes[0] if codes else "",
            "key_length":   key_length,
        }
        save_license(data)
        self.root.destroy()
        run_hub(data)

    def _do_activate(self, code: str):
        """The 'paste a key' box flow: links Discord (if not already) then
        activates this one code against it."""
        import urllib.request, urllib.parse

        def post(url, data=None):
            req = urllib.request.Request(url, data=data,
                                         method="POST" if data is not None else "GET")
            if data is not None:
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read().decode(errors="replace").strip()

        # ── step 1: ask the bot for a Discord-link session ──────────────────
        session = ""
        try:
            resp = post(f"{BOT_BASE}/api/auth/new", data=b"")
            parts = resp.split("|", 2)
            if parts[0] == "OK" and len(parts) >= 3:
                session, auth_url = parts[1], parts[2]
                self.root.after(0, lambda: self._setstatus(
                    "Opening Discord — sign in to link your account…", ui.MUTED))
                webbrowser.open(auth_url)
            elif parts[0] == "OFF":
                session = ""  # linking not required on this bot — skip straight to activate
            else:
                self.root.after(0, lambda: self._activate_done(
                    False, "Couldn't start Discord link. Try again."))
                return
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._activate_done(
                False, f"Server error: {e}"))
            return

        # ── step 2: poll until the browser sign-in completes ────────────────
        if session:
            deadline = time.time() + 120
            linked = False
            while time.time() < deadline:
                try:
                    status_resp = urllib.request.urlopen(
                        f"{BOT_BASE}/api/auth/status?s={urllib.parse.quote(session)}",
                        timeout=10).read().decode(errors="replace").strip()
                except Exception:
                    status_resp = ""
                if status_resp.startswith("LINKED"):
                    linked = True
                    lparts = status_resp.split("|")
                    self._discord_tag = lparts[1] if len(lparts) > 1 else ""
                    self._discord_id  = lparts[2] if len(lparts) > 2 else ""
                    break
                if status_resp.startswith("ERROR") or status_resp == "EXPIRED":
                    reason = status_resp.split("|", 1)[-1] if "|" in status_resp else "Link expired"
                    self.root.after(0, lambda m=reason: self._activate_done(False, m))
                    return
                time.sleep(2)
            if not linked:
                self.root.after(0, lambda: self._activate_done(
                    False, "Discord sign-in timed out. Try again."))
                return
            self.root.after(0, lambda: self._setstatus(
                "Linked! Activating your key…", ui.MUTED))

        # ── step 3: activate the key against the (now linked) session ───────
        ok, result = self._raw_activate(code, session)
        if ok:
            self.root.after(0, lambda: self._activate_done(True, code=code, macro=result))
        else:
            self.root.after(0, lambda: self._activate_done(False, result))

    def _activate_done(self, ok: bool, message: str = "", code: str = "", macro: str = ""):
        self.key_btn.configure(state="normal")
        self.btn.configure(state="normal")
        if not ok:
            self._setstatus(f"❌  {message}", ui.RED)
            return
        owned = {mid: code for mid in MACRO_IDS} if macro == "all" else {macro: code}
        self._login_finish(owned)

    def _init_pts(self):
        # a static starfield, not drifting embers — real stars don't all
        # float upward together. Mix of sizes/brightness gives it depth
        # without needing a second drawn layer or a redraw loop.
        shades = [ui.MUTED, ui.FG2, ui.FG, ui.FG]  # weighted toward brighter
        for _ in range(70):
            self._pts.append({"x": random.randint(0, WIN_W),
                               "y": random.randint(0, WIN_H),
                               "r": random.choice([1, 1, 1, 2]),
                               "shade": random.choice(shades), "id": None})

    def _anim_pts(self):
        for p in self._pts:
            p["id"] = self.cv.create_oval(
                p["x"]-p["r"], p["y"]-p["r"],
                p["x"]+p["r"], p["y"]+p["r"],
                fill=p["shade"], outline="")
        # a handful twinkle very slowly — the only motion here besides the
        # one-shot scanline, so it reads as a sky, not a particle effect
        self.root.after(1800, self._twinkle)

    def _twinkle(self):
        if not self._pts:
            return
        p = random.choice(self._pts)
        p["shade"] = random.choice([ui.MUTED, ui.FG2, ui.FG, ui.FG])
        if p["id"]: self.cv.delete(p["id"])
        p["id"] = self.cv.create_oval(p["x"]-p["r"], p["y"]-p["r"],
                            p["x"]+p["r"], p["y"]+p["r"],
                            fill=p["shade"], outline="")
        self.root.after(1800, self._twinkle)

    def run(self): self.root.mainloop()


# ═══════════════════════════════════════════════════════════════════════════════
#  HUB WINDOW
# ═══════════════════════════════════════════════════════════════════════════════
class HubWindow(Draggable):
    def __init__(self, lic: dict):
        _check_debugger()                          # re-check when hub opens
        self.lic     = lic
        self.procs   = {}
        self.linked  = lic.get("linked", False)
        self._discord_id = lic.get("discord_id", "")
        # only the macros an active key actually covers — never show a tab
        # for something the account hasn't bought
        self.owned_macros = lic.get("owned_macros") or {}
        self.owned_list = [m for m in MACROS if m["id"] in self.owned_macros] or MACROS

        # ── generate session token (also sent to child procs) ─────────────
        global SESSION_TOKEN
        SESSION_TOKEN = _make_session_token(self._discord_id, HWID)

        self.root    = tk.Tk()
        self.root.overrideredirect(True)
        self.root.geometry(f"{HUB_W}x{HUB_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=ui.BG)
        self._center()
        self.root.after(60, lambda: ui.round_window(self.root, HUB_W, HUB_H, 28))
        try: self.root.attributes("-topmost", True)
        except Exception: pass
        self._build()
        self._fetch_key_lengths()

        # heartbeat — checks server every 5 min; kills hub if revoked
        self._start_heartbeat()

    def _center(self):
        self.root.update_idletasks()
        sw, sh = ui.primary_screen_size(self.root)
        self.root.geometry(f"{HUB_W}x{HUB_H}+{(sw-HUB_W)//2}+{(sh-HUB_H)//2}")

    # ── key length ─────────────────────────────────────────────────────────────
    def _fetch_key_lengths(self):
        """Asks the bot for each owned macro's length/expiry and fills the
        KEY row on its panel — one request per macro since different macros
        can be on different keys with different lengths."""
        for m in self.owned_list:
            code = self.owned_macros.get(m["id"])
            if not code:
                continue
            threading.Thread(target=self._fetch_one_key_length,
                             args=(m["id"], code), daemon=True).start()

    def _fetch_one_key_length(self, mid: str, code: str):
        try:
            params = urllib.parse.urlencode({"code": code})
            with urllib.request.urlopen(f"{BOT_BASE}/api/keyinfo?{params}", timeout=10) as r:
                text = r.read().decode(errors="replace").strip()
        except Exception:
            text = ""
        parts = text.split("|")
        length = parts[2] if len(parts) > 2 and parts[0] == "OK" else ""
        self.root.after(0, lambda: self._apply_key_length(mid, length))

    def _apply_key_length(self, mid: str, length: str):
        m = next((x for x in self.owned_list if x["id"] == mid), None)
        if not m or "_keylen" not in m:
            return
        if length:
            colour = ui.RED if length in ("Expired",) else ui.FG2
            m["_keylen"].configure(text=length, fg=colour)
        else:
            m["_keylen"].configure(text="Unknown", fg=ui.MUTED)

    # ── heartbeat ──────────────────────────────────────────────────────────────
    def _start_heartbeat(self):
        license_key = self.lic.get("license_key", "")

        def _beat():
            if license_key:
                ok = key_still_valid(license_key)
            else:
                ok = heartbeat(self._discord_id, SESSION_TOKEN)
            def _apply():
                if not ok:
                    # server revoked — kill everything and reopen login
                    self._close_all_macros()
                    self.root.destroy()
                    wipe_license()
                    tk.messagebox.showerror(
                        "XZX Launcher",
                        "Your session was revoked by the server.\n"
                        "Please contact support on Discord.")
                    run_login()
            self.root.after(0, _apply)
        def _schedule():
            threading.Thread(target=_beat, daemon=True).start()
            self.root.after(5 * 60 * 1000, _schedule)   # every 5 min
        self.root.after(5 * 60 * 1000, _schedule)        # first check after 5 min

    # ── build ──────────────────────────────────────────────────────────────────
    def _build(self):
        # one flat black layer — no gradient, nothing floating behind it
        bgcv = tk.Canvas(self.root, width=HUB_W, height=HUB_H,
                         highlightthickness=0, bg=ui.BG)
        bgcv.place(x=0, y=0)

        # ── custom title bar ──────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=ui.PANEL2,
                       highlightbackground=ui.BORDER, highlightthickness=1,
                       height=34)
        bar.place(x=0, y=0, width=HUB_W, height=34)
        self._make_draggable(bar)

        lc = tk.Canvas(bar, width=110, height=28, bg=ui.PANEL2,
                       highlightthickness=0)
        lc.place(x=16, rely=0.5, anchor="w")
        self.root.after(20, lambda: self._draw_logo_bar(lc))

        # window controls (right side, inset so the close button clears
        # the rounded top-right corner instead of sitting flush in it)
        controls = [
            ("─", lambda: self.root.iconify(), ui.MUTED),
            ("✕", self._close,                  ui.RED),
        ]
        for i, (txt, cmd, hov) in enumerate(controls):
            is_last = i == len(controls) - 1
            lb = tk.Label(bar, text=txt, bg=ui.PANEL2, fg=ui.MUTED,
                          font=("Segoe UI", 11), cursor="hand2",
                          padx=10 if not is_last else 16)
            lb.pack(side="right")
            lb.bind("<Button-1>", lambda e, c=cmd: c())
            lb.bind("<Enter>",    lambda e, l=lb, h=hov: l.configure(fg=h))
            lb.bind("<Leave>",    lambda e, l=lb:  l.configure(fg=ui.MUTED))

        tag = self.lic.get("tag") or self.lic.get("username","User")
        tk.Label(bar, text=f"@{tag}", bg=ui.PANEL2, fg=ui.MUTED,
                 font=("Segoe UI Semibold", 8)).pack(side="right", padx=(0,4))

        # ── sidebar ───────────────────────────────────────────────────────
        SB_Y = 34
        sb = tk.Frame(self.root, bg=ui.PANEL2,
                      highlightbackground=ui.BORDER, highlightthickness=1)
        sb.place(x=0, y=SB_Y, width=ui.SIDEBAR_W, height=HUB_H-SB_Y)
        sb.pack_propagate(False)

        logo_sb = tk.Canvas(sb, width=ui.SIDEBAR_W, height=60,
                            bg=ui.PANEL2, highlightthickness=0)
        logo_sb.pack(fill="x")
        self.root.after(25, lambda: self._draw_logo_sidebar(logo_sb))
        tk.Frame(sb, bg=ui.BORDER, height=1).pack(fill="x")

        self._sbtns = {}
        for m in self.owned_list:
            btn = ui.SideIconBtn(sb, m["icon"], m["name"],
                                  active_color=m["color"])
            btn.frame.pack(fill="x")
            btn.bind("<Button-1>", lambda e, mid=m["id"]: self._show(mid))
            self._sbtns[m["id"]] = btn

        # Theme tab — not a macro, lives in the same sidebar/panel switcher
        theme_btn = ui.SideIconBtn(sb, "🎨", "Theme", active_color=ui.ACCENT)
        theme_btn.frame.pack(fill="x")
        theme_btn.bind("<Button-1>", lambda e: self._show("theme"))
        self._sbtns["theme"] = theme_btn

        tk.Frame(sb, bg=ui.BORDER, height=1).pack(fill="x", pady=(8,0))

        # bottom spacer so nothing sits inside the rounded bottom-left corner
        tk.Frame(sb, bg=ui.PANEL2, height=14).pack(fill="x", side="bottom")

        # Discord support link (bottom, above the account-link icon)
        support_btn = tk.Frame(sb, bg=ui.PANEL2, width=ui.SIDEBAR_W, height=ui.SIDEBAR_W)
        support_btn.pack_propagate(False)
        support_btn.pack(fill="x", side="bottom")
        support_ico = tk.Label(support_btn, text="🎫", bg=ui.PANEL2, fg=ui.MUTED,
                               font=ui.FONT_ICON, cursor="hand2")
        support_ico.place(relx=0.5, rely=0.5, anchor="center")
        support_btn.bind("<Button-1>", lambda e: webbrowser.open(DISCORD_URL))
        support_ico.bind("<Button-1>", lambda e: webbrowser.open(DISCORD_URL))
        support_ico.bind("<Enter>", lambda e: support_ico.configure(fg=ui.FG))
        support_ico.bind("<Leave>", lambda e: support_ico.configure(fg=ui.MUTED))

        # Discord link sidebar icon (bottom)
        self._disc_btn = tk.Frame(sb, bg=ui.PANEL2,
                                   width=ui.SIDEBAR_W, height=ui.SIDEBAR_W)
        self._disc_btn.pack_propagate(False)
        self._disc_btn.pack(fill="x", side="bottom")
        self._disc_ico = tk.Label(self._disc_btn, text="🔗",
                                   bg=ui.PANEL2, fg=ui.MUTED,
                                   font=ui.FONT_ICON, cursor="hand2")
        self._disc_ico.place(relx=0.5, rely=0.5, anchor="center")
        self._disc_bar = tk.Frame(self._disc_btn, bg=ui.PANEL2, width=3)
        self._disc_bar.place(x=0, y=0, relheight=1)
        self._disc_btn.bind("<Button-1>", lambda e: self._link_discord())
        self._disc_ico.bind("<Button-1>", lambda e: self._link_discord())

        # ── content area ──────────────────────────────────────────────────
        cx = ui.SIDEBAR_W + 1
        cw = HUB_W - cx
        ch = HUB_H - SB_Y
        self.cont = tk.Frame(self.root, bg=ui.PANEL)
        self.cont.place(x=cx, y=SB_Y, width=cw, height=ch)

        self._panels = {}
        for m in self.owned_list:
            self._panels[m["id"]] = self._mk_panel(m, cw, ch)
        self._panels["theme"] = self._mk_theme_panel(cw, ch)
        self._link_panel = self._mk_link_panel(cw, ch)

        if self.linked:
            self._show(MACROS[0]["id"])
            self._mark_linked()
        else:
            self._show_link_prompt()

    def _draw_logo_bar(self, cv):
        cy = 14
        cv.create_text(22, cy, text="XZX LAUNCHER", fill=ui.FG,
                       font=("Segoe UI Black", 8), anchor="w")

    def _draw_logo_sidebar(self, cv):
        cx, cy = ui.SIDEBAR_W//2, 30
        cv.create_text(cx, cy, text="XZX", fill=ui.FG,
                       font=("Segoe UI Black", 10))

    # ── Discord link panel ─────────────────────────────────────────────────────
    def _mk_link_panel(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        f.pack_propagate(False)

        # accent top strip
        tk.Frame(f, bg=ui.ACCENT, height=3).pack(fill="x")

        body = tk.Frame(f, bg=ui.PANEL)
        body.place(relx=0.5, rely=0.5, anchor="center")

        # icon in a rounded card
        ico_card = ui.RoundFrame(body, bg=ui.PANEL2, border=ui.BORDER, border_w=1, r=18)
        ico_card.pack(pady=(0,18))
        tk.Label(ico_card.body, text="🔗", bg=ui.PANEL2, fg=ui.ACCENT,
                 font=("Segoe UI Emoji", 34), padx=22, pady=14).pack()
        ico_card.sync_size()

        tk.Label(body, text="Link Your Discord Account",
                 bg=ui.PANEL, fg=ui.FG,
                 font=("Segoe UI Black", 14)).pack()
        tk.Label(body,
                 text="Link your Discord before using any macros.\nConfirms your purchase is tied to this account.",
                 bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI", 9), justify="center").pack(pady=(6,20))

        # logged-in pill
        did = self._discord_id
        id_card = ui.RoundFrame(body, bg=ui.PANEL2, border=ui.BORDER, border_w=1, r=10)
        id_card.pack(pady=(0,20))
        tk.Label(id_card.body, text=f"🆔  {did}",
                 bg=ui.PANEL2, fg=ui.MUTED, font=("Consolas", 9),
                 padx=10, pady=6).pack()
        id_card.sync_size()

        btn = tk.Button(body, text="  🔗   Link Discord Account  ",
                        bg=ui.ACCENT2, fg=ui.BG,
                        activebackground="#e2e2e2", activeforeground=ui.BG,
                        relief="flat", font=("Segoe UI Black", 11),
                        cursor="hand2", pady=13,
                        command=self._link_discord)
        btn.pack(fill="x", padx=20)
        btn.bind("<Enter>", lambda e: btn.configure(bg="#e2e2e2"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=ui.ACCENT2))

        self._link_status = tk.Label(body, text="",
                                      bg=ui.PANEL, fg=ui.MUTED,
                                      font=("Segoe UI", 8))
        self._link_status.pack(pady=(10,0))

        poll_btn = tk.Button(body, text="✓   I've linked — check now",
                             bg=ui.PANEL2, fg=ui.MUTED,
                             activebackground=ui.PANEL3, activeforeground=ui.FG,
                             relief="flat", font=("Segoe UI", 9),
                             cursor="hand2", pady=9,
                             highlightbackground=ui.BORDER2, highlightthickness=1,
                             command=self._check_linked)
        poll_btn.pack(fill="x", padx=20, pady=(8,0))

        tk.Label(body, text="Opens your browser to authorise. Come back when done.",
                 bg=ui.PANEL, fg=ui.BORDER, font=("Segoe UI", 7)).pack(pady=(10,0))
        return f

    # ── theme panel (full-palette customisation) ─────────────────────────────────
    _THEME_GROUPS = [
        ("Backgrounds", [("BG","Window background"), ("GRAD1","Gradient start"),
                          ("GRAD2","Gradient middle"), ("GRAD3","Gradient end"),
                          ("PANEL","Panel"), ("PANEL2","Panel (dark)"),
                          ("PANEL3","Panel (light)")]),
        ("Accent", [("ACCENT","Primary accent"), ("ACCENT2","Accent (dim)"),
                    ("ACCENT3","Accent (muted)")]),
        ("Text", [("FG","Primary text"), ("FG2","Secondary text"),
                  ("MUTED","Muted text")]),
        ("Borders", [("BORDER","Border"), ("BORDER2","Border (faint)")]),
        ("Status", [("GREEN","Success / running"), ("GREEN2","Success (dim)"),
                    ("RED","Error / stop"), ("RED2","Error (dim)")]),
        ("Macro accents", [("CYAN","Fishing accent"), ("ORANGE","Orange accent"),
                            ("YELLOW","Yellow accent")]),
    ]

    def _mk_theme_panel(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        f.pack_propagate(False)

        tk.Frame(f, bg=ui.ACCENT, height=3).pack(fill="x")

        hdr = tk.Frame(f, bg=ui.PANEL2, height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="🎨", bg=ui.PANEL2, fg=ui.ACCENT,
                 font=("Segoe UI Emoji", 22)).place(x=18, rely=0.5, anchor="w")
        info = tk.Frame(hdr, bg=ui.PANEL2)
        info.place(x=58, rely=0.5, anchor="w")
        tk.Label(info, text="APPEARANCE", bg=ui.PANEL2, fg=ui.FG,
                 font=("Segoe UI Black", 13)).pack(anchor="w")
        tk.Label(info, text="Changes every colour here, the Hub and all 3 macros",
                 bg=ui.PANEL2, fg=ui.MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        reset_btn = tk.Button(hdr, text="Reset to default", bg=ui.PANEL2, fg=ui.MUTED,
                              activebackground=ui.PANEL3, activeforeground=ui.FG,
                              relief="flat", font=("Segoe UI", 8), cursor="hand2",
                              padx=10, pady=4, highlightbackground=ui.BORDER2,
                              highlightthickness=1, command=self._reset_theme)
        reset_btn.place(relx=1.0, rely=0.5, anchor="e", x=-14)

        tk.Frame(f, bg=ui.BORDER2, height=1).pack(fill="x")

        # live preview — updates on every reload, so a colour change is visible without
        # leaving this tab or opening a macro window
        prev = tk.Frame(f, bg=ui.PANEL2); prev.pack(fill="x")
        prev_inner = tk.Frame(prev, bg=ui.PANEL2); prev_inner.pack(fill="x", padx=20, pady=12)
        tk.Label(prev_inner, text="PREVIEW", bg=ui.PANEL2, fg=ui.MUTED,
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Button(prev_inner, text="▶  START", bg=ui.ACCENT, fg=ui.BG,
                  activebackground="#e2e2e2", activeforeground=ui.BG,
                  relief="flat", font=("Segoe UI Black", 9), cursor="hand2",
                  padx=16, pady=6, state="disabled",
                  disabledforeground=ui.BG).pack(side="right")
        tk.Frame(f, bg=ui.BORDER2, height=1).pack(fill="x")

        outer, body = ui.scroll_frame(f, bg=ui.PANEL)
        outer.pack(fill="both", expand=True, padx=4, pady=4)
        pad = tk.Frame(body, bg=ui.PANEL, width=20); pad.pack(side="left", fill="y")
        col = tk.Frame(body, bg=ui.PANEL); col.pack(side="left", fill="both", expand=True, pady=14)

        for title, rows in self._THEME_GROUPS:
            ui.section_label(col, title, bg=ui.PANEL)
            for key, label in rows:
                self._mk_colour_row(col, key, label)
        tk.Frame(col, bg=ui.PANEL, height=14).pack()
        return f

    def _mk_colour_row(self, parent, key, label):
        r = tk.Frame(parent, bg=ui.PANEL); r.pack(fill="x", pady=4, padx=(0,16))
        tk.Label(r, text=label, bg=ui.PANEL, fg=ui.FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Label(r, text=getattr(ui, key), bg=ui.PANEL, fg=ui.MUTED,
                 font=("Consolas", 8)).pack(side="right", padx=(0,8))
        swatch = tk.Label(r, text="  ", bg=getattr(ui, key),
                          highlightbackground=ui.BORDER, highlightthickness=1,
                          cursor="hand2", padx=12, pady=4)
        swatch.pack(side="right")
        swatch.bind("<Button-1>", lambda e, k=key: self._pick_colour(k))

    def _pick_colour(self, key):
        current = getattr(ui, key)
        try:
            _, hexv = colorchooser.askcolor(color=current, parent=self.root,
                                            title=f"Choose {key}")
        except Exception:
            hexv = None
        if not hexv:
            return
        overrides = ui.load_theme_overrides()
        overrides[key] = hexv
        ui.save_theme_overrides(overrides)
        self._reload_theme_live()

    def _reset_theme(self):
        ui.save_theme_overrides({})
        self._reload_theme_live()

    def _reload_theme_live(self):
        """Reload the shared ui module so every colour — including the ones
        baked into widget-class defaults, not just explicit ui.X lookups —
        picks up the change, then rebuild this window from scratch so it's
        visible immediately. The 3 macro windows are separate processes;
        each notices the theme file changed on its own poll and does the
        same (see _watch_theme in xzx_fishing.py / xzx_metal.py / xzx_altclick.py)."""
        ui.reload_theme_module()
        self._rebuild()

    def _rebuild(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=ui.BG)
        self._build()
        self._show(getattr(self, "_active_tab", "theme"))

    # ── macro panel ────────────────────────────────────────────────────────────
    def _mk_panel(self, m, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        f.pack_propagate(False)

        # ── coloured top accent strip ─────────────────────────────────────
        tk.Frame(f, bg=m["color"], height=3).pack(fill="x")

        # ── header row ────────────────────────────────────────────────────
        hdr = tk.Frame(f, bg=ui.PANEL2, height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)

        # big icon
        tk.Label(hdr, text=m["icon"], bg=ui.PANEL2, fg=m["color"],
                 font=("Segoe UI Emoji", 22)).place(x=18, rely=0.5, anchor="w")

        # name + desc stacked
        info = tk.Frame(hdr, bg=ui.PANEL2)
        info.place(x=58, rely=0.5, anchor="w")
        tk.Label(info, text=m["name"].upper(), bg=ui.PANEL2, fg=ui.FG,
                 font=("Segoe UI Black", 13)).pack(anchor="w")
        tk.Label(info, text=m["desc"], bg=ui.PANEL2, fg=ui.MUTED,
                 font=("Segoe UI", 8), wraplength=w-180).pack(anchor="w")

        # status badge top-right
        badge = ui.StatusBadge(hdr, bg=ui.PANEL2)
        badge.frame.place(relx=1.0, rely=0.5, anchor="e", x=-14)
        m["_badge"] = badge

        tk.Frame(f, bg=ui.BORDER2, height=1).pack(fill="x")

        body = tk.Frame(f, bg=ui.PANEL)
        body.pack(fill="both", expand=True, padx=24, pady=18)

        # ── hotkeys row ───────────────────────────────────────────────────
        kr = tk.Frame(body, bg=ui.PANEL)
        kr.pack(anchor="w", pady=(0, 16))
        tk.Label(kr, text="HOTKEYS", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI Semibold", 7)).pack(side="left", padx=(0,10))
        for k in m["keys"]:
            tk.Label(kr, text=k, bg=ui.PANEL2, fg=ui.FG2,
                     font=("Consolas", 8), padx=8, pady=4,
                     highlightbackground=m["color"],
                     highlightthickness=1).pack(side="left", padx=3)

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(0,16))

        # ── big LAUNCH button ─────────────────────────────────────────────
        # every macro accent colour is light now (white/grey/green) — black
        # text reads on all of them, unlike the old mixed light/dark palette
        fg_col = ui.BG
        lb = tk.Button(body, text=f"▶   LAUNCH  {m['name'].upper()}",
                       bg=m["color"], fg=fg_col,
                       activebackground=ui.ACCENT, activeforeground=ui.BG,
                       relief="flat", font=("Segoe UI Black", 12),
                       cursor="hand2", pady=14,
                       command=lambda mid=m["id"]: self._launch(mid))
        lb.pack(fill="x")
        lb.bind("<Enter>", lambda e, b=lb: b.configure(bg=ui.ACCENT))
        lb.bind("<Leave>", lambda e, b=lb, c=m["color"]: b.configure(bg=c))

        # ── STOP button (smaller, ghost style) ───────────────────────────
        sb2 = tk.Button(body, text="■   STOP MACRO",
                        bg=ui.PANEL2, fg=ui.RED,
                        activebackground=ui.RED, activeforeground="white",
                        relief="flat", font=("Segoe UI Semibold", 10),
                        cursor="hand2", pady=9,
                        highlightbackground=ui.BORDER2, highlightthickness=1,
                        command=lambda mid=m["id"]: self._stop(mid))
        sb2.pack(fill="x", pady=(8, 0))

        # ── status row ────────────────────────────────────────────────────
        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(16,8))
        sr = tk.Frame(body, bg=ui.PANEL)
        sr.pack(fill="x")
        tk.Label(sr, text="STATUS", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI Semibold", 7)).pack(side="left")
        m["_pid"] = tk.Label(sr, text="Not running", bg=ui.PANEL,
                              fg=ui.MUTED, font=("Segoe UI", 8))
        m["_pid"].pack(side="left", padx=10)

        # ── key length row — filled in once /api/keyinfo answers ───────────
        kyr = tk.Frame(body, bg=ui.PANEL)
        kyr.pack(fill="x", pady=(6, 0))
        tk.Label(kyr, text="KEY", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI Semibold", 7)).pack(side="left")
        m["_keylen"] = tk.Label(kyr, text="Checking…", bg=ui.PANEL,
                                fg=ui.MUTED, font=("Segoe UI", 8))
        m["_keylen"].pack(side="left", padx=10)
        return f

    # ── Discord link flow ──────────────────────────────────────────────────────
    def _show_link_prompt(self):
        for p in self._panels.values(): p.place_forget()
        self._link_panel.place(x=0, y=0, relwidth=1, relheight=1)
        self._link_panel.lift()
        for btn in self._sbtns.values(): btn.set_active(False)
        self._disc_ico.configure(fg=ui.ACCENT)
        self._disc_bar.configure(bg=ui.ACCENT)
        self._disc_btn.configure(bg=ui.PANEL)
        self._disc_ico.configure(bg=ui.PANEL)

    def _link_discord(self):
        params = urllib.parse.urlencode({"discord_id": self._discord_id,
                                         "hwid": HWID})
        webbrowser.open(f"https://api.xzxsuite.com/discord/link?{params}")

    def _check_linked(self):
        self._link_status.configure(text="Checking…", fg=ui.MUTED)
        threading.Thread(target=self._poll_linked, daemon=True).start()

    def _poll_linked(self):
        ok = check_linked(self._discord_id)
        def done():
            if ok:
                self.linked = True
                self.lic["linked"] = True
                save_license(self.lic)
                self._mark_linked()
                self._show(MACROS[0]["id"])
            else:
                self._link_status.configure(
                    text="Not linked yet — open Discord and authorise.", fg=ui.RED)
        self.root.after(0, done)

    def _mark_linked(self):
        self._disc_ico.configure(fg=ui.GREEN)
        self._disc_bar.configure(bg=ui.GREEN)

    # ── navigation ─────────────────────────────────────────────────────────────
    def _show(self, mid):
        _check_debugger()
        if not self.linked:
            self._show_link_prompt(); return
        self._active_tab = mid
        self._link_panel.place_forget()
        for _id, btn in self._sbtns.items(): btn.set_active(_id == mid)
        for _id, panel in self._panels.items():
            if _id == mid:
                panel.place(x=0, y=0, relwidth=1, relheight=1); panel.lift()
            else:
                panel.place_forget()

    # ── launch / stop ──────────────────────────────────────────────────────────
    def _launch(self, mid):
        _check_debugger()
        if not self.linked: return
        if mid in self.procs and self.procs[mid].poll() is None: return
        m    = next(x for x in MACROS if x["id"] == mid)
        # Regenerate the token right before launch instead of reusing the one
        # made at login — it bakes in a 5-min time window, so a Hub left open
        # for a test session (common — exactly what was happening here) would
        # hand every later launch a token that's already aged out, and the
        # macro would reject it as "Invalid session" even on a legit account.
        global SESSION_TOKEN
        SESSION_TOKEN = _make_session_token(self._discord_id, HWID)
        env  = {
            **os.environ,
            # all three must match for child to start
            "XZX_KEY":           self._discord_id,
            "XZX_DISCORD_ID":    self._discord_id,
            "XZX_HWID":          HWID,
            "XZX_SESSION_TOKEN": SESSION_TOKEN,
        }
        # Running as a packaged exe: sys.executable IS "XZX Launcher.exe" here,
        # so `[sys.executable, script.py]` would just relaunch the launcher
        # itself with a stray argument — the macro would never actually start.
        # Run the macro's own sibling exe directly instead. When running from
        # source (python xzx_launcher.py) there's no macro exe to find, so
        # fall back to the old python-interpreter behaviour.
        base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) \
                   else Path(__file__).parent
        exe_path = base_dir / m["exe"]
        if getattr(sys, "frozen", False) and exe_path.exists():
            cmd = [str(exe_path)]
        else:
            cmd = [sys.executable, str(Path(__file__).parent / m["file"])]
        try:
            proc = subprocess.Popen(cmd, env=env)
        except Exception as exc:
            m["_badge"].set("Launch failed", ui.RED)
            m["_pid"].configure(text=f"❌ {exc}"[:60], fg=ui.RED)
            return
        self.procs[mid] = proc
        m["_badge"].set("Running", ui.GREEN)
        m["_pid"].configure(text=f"PID {proc.pid}", fg=ui.GREEN)
        self.root.after(1200, lambda: self._poll(mid))

    def _stop(self, mid):
        proc = self.procs.get(mid)
        if proc and proc.poll() is None: proc.terminate()
        self._mark_stopped(mid)

    def _poll(self, mid):
        proc = self.procs.get(mid)
        if proc and proc.poll() is None:
            self.root.after(1200, lambda: self._poll(mid))
        else:
            self._mark_stopped(mid)

    def _mark_stopped(self, mid):
        m = next(x for x in MACROS if x["id"] == mid)
        m["_badge"].set("Stopped", ui.MUTED)
        m["_pid"].configure(text="Not running", fg=ui.MUTED)

    def _close_all_macros(self):
        for mid in list(self.procs): self._stop(mid)

    def _close(self):
        self._close_all_macros()
        self.root.destroy()

    def run(self): self.root.mainloop()


# ── entry ───────────────────────────────────────────────────────────────────────
def run_login(): LoginWindow().run()
def run_hub(lic): HubWindow(lic).run()

if __name__ == "__main__":
    _check_debugger()
    # Always go through login — even if cached — to hit server validation
    run_login()
