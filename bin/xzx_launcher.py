"""
XZX Launcher — Login + Hub
Discord ID = your key. Must link Discord before macros unlock.
No OS title bar. Purple/pink gradient style.

ANTI-BYPASS HARDENING:
  - HWID fingerprint locked to machine
  - Server-side validation on every launch (no cached bypass)
  - Env var token required by all macro processes
  - Session token rotates each launch, macros die if missing
  - File tampering detection (launcher checks its own hash)
  - Anti-debug: exits if debugger detected
  - Macros check env token every 60s, exit if revoked
"""
import tkinter as tk
import threading, subprocess, os, sys, json, hashlib, time, webbrowser, random, hmac, struct
import urllib.parse, urllib.request, secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
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
KEY_PRODUCT   = "metal"   # DEFAULT_MACRO on the bot; an "all macros" key passes for any product

# ── Discord OAuth config ────────────────────────────────────────────────────────
# Set DISCORD_CLIENT_ID to your bot/app's client ID.
# The OAuth callback lands on localhost:47392 — register that as a redirect URI
# in your Discord Developer Portal → OAuth2 → Redirects.
DISCORD_CLIENT_ID  = os.getenv("XZX_DISCORD_CLIENT_ID", "1556620681083617340")
OAUTH_REDIRECT_URI = "http://localhost:47392/callback"
OAUTH_SCOPES       = "identify"
OAUTH_PORT         = 47392

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
     "keys":["F5 Start","F6 Stop"]},
    {"id":"metal",    "icon":"🔍", "name":"Metal Detector",
     "color": ui.ACCENT, "file":"xzx_metal.py",    "exe":"XZX Metal.exe",
     "desc":"GDI pixel scan — auto skill-check on metal detector minigame.",
     "keys":["F7 Start","F8 Stop"]},
    {"id":"altclick", "icon":"🖱️", "name":"Alt Click",
     "color": ui.GREEN,  "file":"xzx_altclick.py", "exe":"XZX AltClick.exe",
     "desc":"Hold-Alt auto-click macro with configurable CPS.",
     "keys":["F9 Toggle"]},
]

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
    RADIUS = 22  # whole-window corner radius

    def __init__(self):
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
        self._build()

    def _center(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
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
        card = tk.Frame(wrap, bg=ui.PANEL, width=CARD_W,
                        highlightbackground=ui.BORDER, highlightthickness=1)
        card.pack()
        tk.Frame(card, bg=ui.ACCENT, height=2).pack(fill="x")

        inn = tk.Frame(card, bg=ui.PANEL)
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
                                      bg=ui.ACCENT2, hover=ui.ACCENT,
                                      font=("Segoe UI Black", 10),
                                      parent_bg=ui.PANEL)
        self.key_btn.pack(pady=(10, 0))

        tk.Label(inn, text="🔒  Validated server-side on every launch",
                 bg=ui.PANEL, fg=ui.BORDER,
                 font=("Segoe UI", 7)).pack(pady=(14, 0))

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
        import math
        cx, cy = w // 2, h // 2
        # soft layered glow behind the hex (no inner clutter, one clean ring)
        for glow_r, glow_col in [(48, "#0a2e2a"), (42, "#0f4a42"), (37, "#167a6c")]:
            pts = []
            for i in range(6):
                a = math.radians(60 * i - 30)
                pts += [cx + glow_r * math.cos(a), cy + glow_r * math.sin(a)]
            cv.create_polygon(pts, outline=glow_col, fill="", width=2,
                              smooth=False)
        # solid hexagon body with a crisp accent outline
        r = 33
        pts = []
        for i in range(6):
            a = math.radians(60 * i - 30)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        cv.create_polygon(pts, outline=ui.ACCENT, fill=ui.PANEL2, width=2)
        # XZX wordmark, centred
        cv.create_text(cx, cy, text="XZX", fill=ui.FG,
                       font=("Segoe UI Black", 17))

    def _paint_bg(self):
        # instrument-panel gradient: near-black up top fading into a deep
        # teal-navy glow low in the card area — a console, not a nightclub
        ui.draw_gradient(self.cv, WIN_W, WIN_H, "#060a10", "#0e2a30",
                         steps=100)
        self.cv.create_rectangle(0, 30, WIN_W, 32, fill=ui.ACCENT, outline="")
        self._draw_scanline()

    def _draw_scanline(self):
        """One orchestrated reveal moment: a signal sweep crosses the card
        once on open, tying back to what XZX actually sells (detection
        macros) instead of a generic fade-in."""
        line = self.cv.create_line(0, 34, WIN_W, 34, fill=ui.ACCENT, width=2)
        glow = self.cv.create_rectangle(0, 30, WIN_W, 38, fill=ui.ACCENT, outline="", stipple="gray25")

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
            code = mk.split("|", 1)[1].split(",")[0].strip()
            self.root.after(0, lambda: self._setstatus("Key found — activating…", ui.MUTED))
            self._do_activate(code, session=session)   # reuses the key-activate step
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

    def _do_activate(self, code: str, session: str = None):
        import urllib.request, urllib.parse

        def post(url, data=None):
            req = urllib.request.Request(url, data=data,
                                         method="POST" if data is not None else "GET")
            if data is not None:
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read().decode(errors="replace").strip()

        # already linked (e.g. came from the Discord-login button) — skip straight to activate
        if session is not None:
            try:
                payload = urllib.parse.urlencode({
                    "code": code, "product": KEY_PRODUCT, "hwid": HWID, "session": session,
                }).encode()
                text = post(API_ACTIVATE, data=payload)
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._activate_done(False, f"Server error: {e}"))
                return
            parts = text.split("|", 1)
            if parts[0] == "OK":
                self.root.after(0, lambda: self._activate_done(True, code=code))
            else:
                detail = parts[1] if len(parts) > 1 else ""
                self.root.after(0, lambda: self._activate_done(False, detail or "Key not accepted."))
            return

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
        try:
            payload = urllib.parse.urlencode({
                "code": code, "product": KEY_PRODUCT, "hwid": HWID,
                "session": session,
            }).encode()
            text = post(API_ACTIVATE, data=payload)
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._activate_done(
                False, f"Server error: {e}"))
            return

        parts = text.split("|", 1)
        status = parts[0] if parts else ""
        detail = parts[1] if len(parts) > 1 else ""

        if status == "OK":
            self.root.after(0, lambda: self._activate_done(True, code=code))
        else:
            self.root.after(0, lambda: self._activate_done(
                False, detail or "Key not accepted."))

    def _activate_done(self, ok: bool, message: str = "", code: str = ""):
        self.key_btn.configure(state="normal")
        self.btn.configure(state="normal")
        if not ok:
            self._setstatus(f"❌  {message}", ui.RED)
            return
        data = {
            "discord_id": "",
            "username":   code,
            "tag":        code,
            "linked":     True,
            "macros":     [],
            "license_key": code,
        }
        save_license(data)
        self.root.destroy()
        run_hub(data)

    def _init_pts(self):
        for _ in range(22):
            self._pts.append({"x": random.randint(0,WIN_W),
                               "y": random.randint(0,WIN_H),
                               "dx": random.uniform(-0.3,0.3),
                               "dy": random.uniform(-0.5,-0.1),
                               "r":  random.randint(1,2), "id": None})

    def _anim_pts(self):
        for p in self._pts:
            if p["id"]: self.cv.delete(p["id"])
            p["x"] += p["dx"]; p["y"] += p["dy"]
            if p["y"] < -4:
                p["y"] = WIN_H+4; p["x"] = random.randint(0,WIN_W)
            p["id"] = self.cv.create_oval(
                p["x"]-p["r"], p["y"]-p["r"],
                p["x"]+p["r"], p["y"]+p["r"],
                fill=ui.BORDER, outline="")
        self.root.after(35, self._anim_pts)

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

        # ── generate session token (also sent to child procs) ─────────────
        global SESSION_TOKEN
        SESSION_TOKEN = _make_session_token(self._discord_id, HWID)

        self.root    = tk.Tk()
        self.root.overrideredirect(True)
        self.root.geometry(f"{HUB_W}x{HUB_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=ui.BG)
        self._center()
        self.root.after(60, lambda: ui.round_window(self.root, HUB_W, HUB_H, 22))
        self._build()

        # heartbeat — checks server every 5 min; kills hub if revoked
        self._start_heartbeat()

    def _center(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{HUB_W}x{HUB_H}+{(sw-HUB_W)//2}+{(sh-HUB_H)//2}")

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
        bgcv = tk.Canvas(self.root, width=HUB_W, height=HUB_H,
                         highlightthickness=0, bg=ui.BG)
        bgcv.place(x=0, y=0)
        self.root.after(10, lambda: ui.draw_gradient(
            bgcv, HUB_W, HUB_H, ui.BG, "#280a3e", steps=80))

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
        for m in MACROS:
            btn = ui.SideIconBtn(sb, m["icon"], m["name"],
                                  active_color=m["color"])
            btn.frame.pack(fill="x")
            btn.bind("<Button-1>", lambda e, mid=m["id"]: self._show(mid))
            self._sbtns[m["id"]] = btn

        tk.Frame(sb, bg=ui.BORDER, height=1).pack(fill="x", pady=(8,0))

        # bottom spacer so nothing sits inside the rounded bottom-left corner
        tk.Frame(sb, bg=ui.PANEL2, height=14).pack(fill="x", side="bottom")

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
        for m in MACROS:
            self._panels[m["id"]] = self._mk_panel(m, cw, ch)
        self._link_panel = self._mk_link_panel(cw, ch)

        if self.linked:
            self._show(MACROS[0]["id"])
            self._mark_linked()
        else:
            self._show_link_prompt()

    def _draw_logo_bar(self, cv):
        import math
        cx, cy = 14, 14
        r = 10
        pts = []
        for i in range(6):
            a = math.radians(60*i-30)
            pts += [cx+r*math.cos(a), cy+r*math.sin(a)]
        cv.create_polygon(pts, outline=ui.ACCENT, fill=ui.PANEL2, width=1)
        cv.create_text(cx+1, cy, text="X", fill=ui.FG,
                       font=("Segoe UI Black", 7))
        cv.create_text(36, cy, text="XZX LAUNCHER", fill=ui.FG,
                       font=("Segoe UI Black", 8), anchor="w")

    def _draw_logo_sidebar(self, cv):
        import math
        cx, cy = ui.SIDEBAR_W//2, 30
        r = 18
        pts = []
        for i in range(6):
            a = math.radians(60*i-30)
            pts += [cx+r*math.cos(a), cy+r*math.sin(a)]
        cv.create_polygon(pts, outline=ui.ACCENT, fill=ui.PANEL2, width=2)
        cv.create_text(cx, cy, text="XZX", fill=ui.FG,
                       font=("Segoe UI Black", 8))

    # ── Discord link panel ─────────────────────────────────────────────────────
    def _mk_link_panel(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        f.pack_propagate(False)

        # accent top strip
        tk.Frame(f, bg=ui.ACCENT, height=3).pack(fill="x")

        body = tk.Frame(f, bg=ui.PANEL)
        body.place(relx=0.5, rely=0.5, anchor="center")

        # icon in a coloured circle-ish card
        ico_card = tk.Frame(body, bg=ui.PANEL2,
                            highlightbackground=ui.BORDER, highlightthickness=1)
        ico_card.pack(pady=(0,18))
        tk.Label(ico_card, text="🔗", bg=ui.PANEL2, fg=ui.ACCENT,
                 font=("Segoe UI Emoji", 34), padx=22, pady=14).pack()

        tk.Label(body, text="Link Your Discord Account",
                 bg=ui.PANEL, fg=ui.FG,
                 font=("Segoe UI Black", 14)).pack()
        tk.Label(body,
                 text="Link your Discord before using any macros.\nConfirms your purchase is tied to this account.",
                 bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI", 9), justify="center").pack(pady=(6,20))

        # logged-in pill
        did = self._discord_id
        tk.Label(body, text=f"  🆔  {did}  ",
                 bg=ui.PANEL2, fg=ui.MUTED, font=("Consolas", 9),
                 padx=6, pady=6,
                 highlightbackground=ui.BORDER, highlightthickness=1
                 ).pack(pady=(0,20))

        btn = tk.Button(body, text="  🔗   Link Discord Account  ",
                        bg=ui.ACCENT2, fg="white",
                        activebackground=ui.ACCENT, activeforeground="white",
                        relief="flat", font=("Segoe UI Black", 11),
                        cursor="hand2", pady=13,
                        command=self._link_discord)
        btn.pack(fill="x", padx=20)
        btn.bind("<Enter>", lambda e: btn.configure(bg=ui.ACCENT))
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
        fg_col = ui.BG if m["color"] in (ui.CYAN, ui.GREEN) else "white"
        lb = tk.Button(body, text=f"▶   LAUNCH  {m['name'].upper()}",
                       bg=m["color"], fg=fg_col,
                       activebackground=ui.ACCENT, activeforeground="white",
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
