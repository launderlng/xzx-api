"""
XZX Metal Detector Macro — black-and-white detector style
Auto skill-check for the FiveM metal detector minigame.
"""
import tkinter as tk
import tkinter.messagebox
import threading, time, os, sys, ctypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xzx_ui as ui

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

WIN_W, WIN_H = 380, 460   # launches smaller than before — a compact window, not a second Hub

# ── GDI pixel colour ──────────────────────────────────────────────────────────
def _get_pixel(x, y):
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        col = ctypes.windll.gdi32.GetPixel(hdc, x, y)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if col == -1: return None
        r = col & 0xFF; g = (col >> 8) & 0xFF; b = (col >> 16) & 0xFF
        return r, g, b
    except Exception:
        return None

def _colour_match(px, target_rgb, tol=30):
    if px is None: return False
    return all(abs(px[i]-target_rgb[i]) <= tol for i in range(3))

# ── SendInput key press ────────────────────────────────────────────────────────
PUL = ctypes.POINTER(ctypes.c_ulong)

class _KBInput(ctypes.Structure):
    _fields_ = [("wVk",ctypes.c_ushort),("wScan",ctypes.c_ushort),
                ("dwFlags",ctypes.c_ulong),("time",ctypes.c_ulong),
                ("dwExtraInfo",PUL)]

class _InputUnion(ctypes.Union):
    _fields_ = [("ki",_KBInput)]

class _Input(ctypes.Structure):
    _fields_ = [("type",ctypes.c_ulong),("ii",_InputUnion)]

def _press_e():
    try:
        down = _Input(type=1); down.ii.ki.wVk = 0x45
        up   = _Input(type=1); up.ii.ki.wVk   = 0x45; up.ii.ki.dwFlags = 2
        ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(down))
        time.sleep(0.04)
        ctypes.windll.user32.SendInput(1, ctypes.byref(up),   ctypes.sizeof(up))
    except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
class MetalMacro(ui.Draggable):
    def __init__(self):
        self.running = False
        self.hits    = 0
        self._thread = None
        self._hotkeys = ui.HotkeyWatcher()
        self._theme_mtime = ui.theme_mtime()
        self._cur_tab = 0

        self.root = tk.Tk()
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(False, False)
        ui.frameless(self.root, WIN_W, WIN_H, radius=20)
        self._center()
        # Windows resets position once the overrideredirect style change
        # actually lands on the native window — re-apply a moment later.
        self.root.after(30, self._center)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self._hotkey_poll()

    def _hotkey_poll(self):
        # Polled key state, not Tk <bind> — fires while the game window
        # has focus, not just while this macro window is focused.
        if self._hotkeys.pressed(ui.VK_F7): self._start()
        if self._hotkeys.pressed(ui.VK_F8): self._stop()
        if self._hotkeys.pressed(ui.VK_F10):
            self._quit(); return
        m = ui.theme_mtime()
        if m != self._theme_mtime:
            self._theme_mtime = m
            self._rebuild_theme()
        self.root.after(50, self._hotkey_poll)

    def _rebuild_theme(self):
        """Picks up a colour change saved from the Hub's Theme tab and
        rebuilds this window in place, without touching the running scan
        thread or its counters."""
        ui.reload_theme_module()
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=ui.BG)
        self._build()
        self._tab(self._cur_tab)
        if self.running:
            self.badge.set("Running", ui.GREEN)
            self._toggle.btn.configure(text="■  STOP SCANNING", bg=ui.RED, fg="white")
            self._toggle._running = True

    def _center(self):
        self.root.update_idletasks()
        sw, sh = ui.primary_screen_size(self.root)
        self.root.geometry(f"{WIN_W}x{WIN_H}+{(sw-WIN_W)//2}+{(sh-WIN_H)//2}")

    def _build(self):
        M = 36  # margin around the sidebar+content card, all 4 sides — a
                # real band of visible background, not a thin edge sliver
        bgcv = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                         highlightthickness=0, bg=ui.BG)
        bgcv.place(x=0, y=0)
        bgcv.configure(bg=ui.BG)   # flat black — matches the launcher/Hub theme now
        self._make_draggable(bgcv)  # drag from anywhere on the background
        self._snow = ui.Snowfall(bgcv, WIN_W, WIN_H, count=170)
        self._snow.start()

        # header
        self.badge = ui.macro_header(self, WIN_W, "🔍", "METAL DETECTOR", ui.ACCENT)

        # sidebar
        TOP_Y = ui.HEADER_H + 2 + M
        sb = tk.Frame(self.root, bg=ui.PANEL2,
                      highlightthickness=0)
        sb.place(x=M, y=TOP_Y,
                 width=ui.SIDEBAR_W, height=WIN_H-TOP_Y-M)

        self._stabs = []
        for i,(ico,name) in enumerate([("🔍","Macro"),("📊","Stats"),("⚙","Settings")]):
            btn = ui.SideIconBtn(sb, ico, name, active_color=ui.ACCENT)
            btn.frame.pack(fill="x")
            btn.bind("<Button-1>", lambda e, idx=i: self._tab(idx))
            self._stabs.append(btn)

        cx = M+ui.SIDEBAR_W+1
        cw = WIN_W - cx - M
        ch = WIN_H - TOP_Y - M
        self.cont = tk.Frame(self.root, bg=ui.PANEL)
        self.cont.place(x=cx, y=TOP_Y, width=cw, height=ch)

        v0 = self._mk_macro(cw, ch)
        v1 = self._mk_stats(cw, ch)
        v2 = self._mk_settings(cw, ch)
        self._views = [v0, v1, v2]
        self._tab(0)

    def _mk_macro(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        f.pack_propagate(False)
        body = tk.Frame(f, bg=ui.PANEL)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(body, text="🔍", bg=ui.PANEL, fg=ui.ACCENT,
                 font=("Segoe UI Emoji", 36)).pack(pady=(8,4))
        tk.Label(body, text="Metal Detector Macro", bg=ui.PANEL, fg=ui.FG,
                 font=("Segoe UI Black", 14)).pack()
        tk.Label(body, text="GDI pixel scan — auto skill-check",
                 bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI", 9)).pack(pady=(2,16))


        tiles = tk.Frame(body, bg=ui.PANEL)
        tiles.pack(fill="x", pady=12)
        self._t_hits   = ui.StatTile(tiles, "HITS",   "0", accent=ui.GREEN)
        self._t_hits.frame.pack(side="left", expand=True, fill="both", padx=(0,4))
        self._t_misses = ui.StatTile(tiles, "MISSES", "0", accent=ui.RED)
        self._t_misses.frame.pack(side="left", expand=True, fill="both", padx=(4,0))


        self._toggle = ui.ToggleButton(body, self._on_toggle,
                                        start_label="▶  START SCANNING",
                                        stop_label="■  STOP SCANNING",
                                        start_bg=ui.ACCENT2, stop_bg=ui.RED)
        self._toggle.btn.pack(fill="x", ipady=4)

        ui.hotkey_row(body, [("F7","Start"),("F8","Stop"),("F10","Close")],
                      bg=ui.PANEL).pack(fill="x", pady=(10,0))

        self._log, sb2 = ui.log_box(body, height=5)
        sb2.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        return f

    def _mk_stats(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        body = tk.Frame(f, bg=ui.PANEL)
        body.pack(fill="both", expand=True, padx=20, pady=20)
        tk.Label(body, text="SESSION STATS", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w")

        def row(lbl):
            r = tk.Frame(body, bg=ui.PANEL); r.pack(fill="x", pady=4)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.MUTED,
                     font=("Segoe UI",9)).pack(side="left")
            v = tk.StringVar(value="0")
            tk.Label(r, textvariable=v, bg=ui.PANEL, fg=ui.FG,
                     font=("Segoe UI Semibold",9)).pack(side="right")
            return v

        self._sv_hits   = row("Successful skill-checks")
        self._sv_misses = row("Missed checks")
        self._sv_uptime = row("Uptime")
        return f

    def _mk_settings(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        body = tk.Frame(f, bg=ui.PANEL)
        body.pack(fill="both", expand=True, padx=20, pady=20)

        ui.section_label(body, "Screen", bg=ui.PANEL)
        mon_row = tk.Frame(body, bg=ui.PANEL); mon_row.pack(fill="x", pady=5)
        tk.Label(mon_row, text="Monitor", bg=ui.PANEL, fg=ui.FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._monitors = ui.get_monitors()
        self.v_monitor = tk.StringVar(value=self._monitors[0][0])
        ui.dropdown(mon_row, self.v_monitor, [m[0] for m in self._monitors],
                    width=16).pack(side="right")

        ui.section_label(body, "Scan Region (on chosen monitor)", bg=ui.PANEL)

        def row(lbl, default, lo, hi):
            r = tk.Frame(body, bg=ui.PANEL); r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.FG2,
                     font=("Segoe UI",9)).pack(side="left")
            v = tk.IntVar(value=default)
            ui.spinbox(r, v, lo, hi, step=1, width=7).pack(side="right")
            return v

        self.v_scan_x = row("Scan X",  900, 0, 3840)
        self.v_scan_y = row("Scan Y",  500, 0, 2160)


        def rowf(parent, lbl, default, lo, hi):
            r = tk.Frame(parent, bg=ui.PANEL); r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.FG2,
                     font=("Segoe UI",9)).pack(side="left")
            v = tk.IntVar(value=default)
            ui.spinbox(r, v, lo, hi, step=5, width=7).pack(side="right")
            return v

        self.v_tol = rowf(body, "Colour tolerance", 30, 5, 80)

        # Exact target RGB is a one-time calibration most people never touch
        # again — tucked behind "Advanced" instead of 3 more fields showing
        # by default.
        adv_wrap, adv = ui.collapsible(body, "Advanced — exact target colour", bg=ui.PANEL)
        adv_wrap.pack(fill="x", pady=(6, 0))
        self.v_r = rowf(adv, "Target R", 255, 0, 255)
        self.v_g = rowf(adv, "Target G", 220, 0, 255)
        self.v_b = rowf(adv, "Target B",  50, 0, 255)
        return f

    def _monitor_offset(self):
        label = self.v_monitor.get()
        for name, left, top, mw, mh in self._monitors:
            if name == label: return left, top
        return 0, 0

    def _tab(self, idx):
        self._cur_tab = idx
        for i, btn in enumerate(self._stabs): btn.set_active(i == idx)
        for i, v in enumerate(self._views):
            if i == idx: v.place(x=0,y=0,relwidth=1,relheight=1); v.lift()
            else:        v.place_forget()

    def _on_toggle(self, running):
        if running: self._start()
        else:       self._stop()

    def _start(self):
        if self.running: return
        self.running   = True
        self._start_ts = time.time()
        self.badge.set("Running", ui.GREEN)
        self._toggle.btn.configure(text="■  STOP SCANNING", bg=ui.RED, fg="white")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        ui.append_log(self._log, "▶ Scanner started")

    def _stop(self):
        if not self.running: return
        self.running = False
        self.badge.set("Stopped", ui.MUTED)
        self._toggle.force_stop()
        ui.append_log(self._log, "■ Scanner stopped")

    def _loop(self):
        while self.running:
            try:
                tol = self.v_tol.get()
                tgt = (self.v_r.get(), self.v_g.get(), self.v_b.get())
                mon_x, mon_y = self._monitor_offset()
                sx  = mon_x + self.v_scan_x.get()
                sy  = mon_y + self.v_scan_y.get()
                px  = _get_pixel(sx, sy)
                if _colour_match(px, tgt, tol):
                    _press_e()
                    self.hits += 1
                    self._t_hits.set(self.hits)
                    self._sv_hits.set(str(self.hits))
                    ui.append_log(self._log, f"  ✓ Hit #{self.hits}")
                    time.sleep(0.3)
                elapsed = time.time() - self._start_ts
                hh = int(elapsed//3600); mm = int((elapsed%3600)//60); ss = int(elapsed%60)
                self._sv_uptime.set(f"{hh:02d}:{mm:02d}:{ss:02d}")
                time.sleep(0.05)
            except Exception as e:
                ui.append_log(self._log, f"  ⚠ {e}"); time.sleep(1.0)

    def _quit(self): self.running = False; self.root.destroy()
    def run(self):   self.root.mainloop()

if __name__ == "__main__": MetalMacro().run()
