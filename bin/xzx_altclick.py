"""
XZX Alt-Click Macro — black-and-white detector style
Hold Alt → click down → slide right N px → click up → release Alt
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

WIN_W, WIN_H = 380, 440   # launches smaller than before — a compact window, not a second Hub

# ── input helpers ─────────────────────────────────────────────────────────────
PUL = ctypes.POINTER(ctypes.c_ulong)

class KBInput(ctypes.Structure):
    _fields_ = [("wVk",ctypes.c_ushort),("wScan",ctypes.c_ushort),
                ("dwFlags",ctypes.c_ulong),("time",ctypes.c_ulong),
                ("dwExtraInfo",PUL)]

class MInput(ctypes.Structure):
    _fields_ = [("dx",ctypes.c_long),("dy",ctypes.c_long),
                ("mouseData",ctypes.c_ulong),("dwFlags",ctypes.c_ulong),
                ("time",ctypes.c_ulong),("dwExtraInfo",PUL)]

class InputUnion(ctypes.Union):
    _fields_ = [("ki",KBInput),("mi",MInput)]

class Input(ctypes.Structure):
    _fields_ = [("type",ctypes.c_ulong),("ii",InputUnion)]

def _key(vk, up=False):
    try:
        i = Input(type=1); i.ii.ki.wVk=vk; i.ii.ki.dwFlags=2 if up else 0
        ctypes.windll.user32.SendInput(1,ctypes.byref(i),ctypes.sizeof(i))
    except Exception: pass

def _mouse(flags, dx=0, dy=0):
    try:
        i = Input(type=0); i.ii.mi.dwFlags=flags
        i.ii.mi.dx=dx; i.ii.mi.dy=dy
        ctypes.windll.user32.SendInput(1,ctypes.byref(i),ctypes.sizeof(i))
    except Exception: pass

VK_LMENU = 0xA4

def _do_altclick(slide_px=5, hold_ms=80):
    _key(VK_LMENU)           # Alt down
    time.sleep(0.04)
    _mouse(0x0002)            # LMB down
    time.sleep(hold_ms/1000)
    _mouse(0x0001, dx=slide_px, dy=0)  # move right
    _mouse(0x0004)            # LMB up
    time.sleep(0.03)
    _key(VK_LMENU, up=True)  # Alt up


# ─────────────────────────────────────────────────────────────────────────────
class AltClickMacro(ui.Draggable):
    def __init__(self):
        self.running = False
        self.clicks  = 0
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
        if self._hotkeys.pressed(ui.VK_F9): self._toggle()
        if self._hotkeys.pressed(ui.VK_F10):
            self._quit(); return
        m = ui.theme_mtime()
        if m != self._theme_mtime:
            self._theme_mtime = m
            self._rebuild_theme()
        self.root.after(50, self._hotkey_poll)

    def _rebuild_theme(self):
        """Picks up a colour change saved from the Hub's Theme tab and
        rebuilds this window in place, without touching the running click
        thread or its counters."""
        ui.reload_theme_module()
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=ui.BG)
        self._build()
        self._tab(self._cur_tab)
        if self.running:
            self.badge.set("Running", ui.GREEN)
            self._tbtn.btn.configure(text="■  STOP CLICKING", bg=ui.RED, fg="white")
            self._tbtn._running = True

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
        self.badge = ui.macro_header(self, WIN_W, "🖱️", "ALT CLICK MACRO", ui.ACCENT)

        # sidebar
        TOP_Y = ui.HEADER_H + 2 + M
        sb = tk.Frame(self.root, bg=ui.PANEL2,
                      highlightthickness=0)
        sb.place(x=M, y=TOP_Y,
                 width=ui.SIDEBAR_W, height=WIN_H-TOP_Y-M)

        self._stabs = []
        for i,(ico,name) in enumerate([("🖱️","Macro"),("📊","Stats"),("⚙","Settings")]):
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

        tk.Label(body, text="🖱️", bg=ui.PANEL, fg=ui.ACCENT,
                 font=("Segoe UI Emoji", 36)).pack(pady=(8,4))
        tk.Label(body, text="Alt-Click Macro", bg=ui.PANEL, fg=ui.FG,
                 font=("Segoe UI Black", 14)).pack()
        tk.Label(body, text="Auto alt-click with configurable CPS",
                 bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI", 9)).pack(pady=(2,16))


        tiles = tk.Frame(body, bg=ui.PANEL)
        tiles.pack(fill="x", pady=12)
        self._t_clicks = ui.StatTile(tiles, "CLICKS", "0", accent=ui.ACCENT)
        self._t_clicks.frame.pack(side="left", expand=True, fill="both", padx=(0,4))
        self._t_cps = ui.StatTile(tiles, "CPS", "0.0", accent=ui.CYAN)
        self._t_cps.frame.pack(side="left", expand=True, fill="both", padx=(4,0))


        self._tbtn = ui.ToggleButton(body, self._on_toggle,
                                      start_label="▶  START CLICKING",
                                      stop_label="■  STOP CLICKING",
                                      start_bg=ui.ACCENT, stop_bg=ui.RED)
        self._tbtn.btn.pack(fill="x", ipady=4)
        self._tbtn.btn.configure(fg=ui.BG)

        ui.hotkey_row(body, [("F9","Toggle"), ("F10","Close")],
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

        self._sv_clicks = row("Total Clicks")
        self._sv_cps    = row("Avg CPS")
        self._sv_uptime = row("Uptime")
        return f

    def _mk_settings(self, w, h):
        f = tk.Frame(self.cont, bg=ui.PANEL, width=w, height=h)
        body = tk.Frame(f, bg=ui.PANEL)
        body.pack(fill="both", expand=True, padx=20, pady=20)

        def row(parent, lbl, default, lo, hi, step):
            r = tk.Frame(parent, bg=ui.PANEL); r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.FG2,
                     font=("Segoe UI",9)).pack(side="left")
            v = tk.DoubleVar(value=default)
            ui.spinbox(r, v, lo, hi, step=step, width=7).pack(side="right")
            return v

        self.v_cps = row(body, "CPS (clicks/sec)", 8.0, 1.0, 30.0, 0.5)

        self.v_jitter = tk.BooleanVar(value=True)
        jr = tk.Frame(body, bg=ui.PANEL); jr.pack(fill="x", pady=5)
        tk.Label(jr, text="Random jitter", bg=ui.PANEL, fg=ui.FG2,
                 font=("Segoe UI",9)).pack(side="left")
        ui.Switch(jr, self.v_jitter, bg=ui.PANEL).pack(side="right")

        adv_wrap, adv = ui.collapsible(body, "Advanced", bg=ui.PANEL)
        adv_wrap.pack(fill="x")
        self.v_slide   = row(adv, "Slide distance (px)", 5,  1,  30,  1)
        self.v_hold_ms = row(adv, "Hold duration (ms)",  80, 10, 300, 10)
        return f

    def _tab(self, idx):
        self._cur_tab = idx
        for i, btn in enumerate(self._stabs): btn.set_active(i == idx)
        for i, v in enumerate(self._views):
            if i == idx: v.place(x=0,y=0,relwidth=1,relheight=1); v.lift()
            else:        v.place_forget()

    def _on_toggle(self, running):
        if running: self._start()
        else:       self._stop()

    def _toggle(self):
        if self.running: self._stop()
        else:            self._start()

    def _start(self):
        if self.running: return
        self.running   = True
        self._start_ts = time.time()
        self._click_ts = []
        self.badge.set("Running", ui.GREEN)
        self._tbtn.btn.configure(text="■  STOP CLICKING", bg=ui.RED, fg="white")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        ui.append_log(self._log, "▶ Alt-Click started")

    def _stop(self):
        if not self.running: return
        self.running = False
        self.badge.set("Stopped", ui.MUTED)
        self._tbtn.force_stop()
        ui.append_log(self._log, "■ Alt-Click stopped")

    def _loop(self):
        import random
        while self.running:
            try:
                cps     = self.v_cps.get()
                slide   = int(self.v_slide.get())
                hold_ms = int(self.v_hold_ms.get())
                jitter  = self.v_jitter.get()
                interval = 1.0 / max(cps, 0.1)
                if jitter:
                    interval += random.uniform(-0.02, 0.02)
                    slide    += random.randint(-1, 1)

                _do_altclick(slide_px=max(1,slide), hold_ms=hold_ms)
                self.clicks += 1
                now = time.time()
                self._click_ts.append(now)
                self._click_ts = [t for t in self._click_ts if now - t < 5]
                avg_cps = len(self._click_ts) / 5.0

                self._t_clicks.set(self.clicks)
                self._t_cps.set(f"{avg_cps:.1f}")
                self._sv_clicks.set(str(self.clicks))
                self._sv_cps.set(f"{avg_cps:.1f}")

                elapsed = now - self._start_ts
                hh = int(elapsed//3600)
                mm = int((elapsed%3600)//60)
                ss = int(elapsed%60)
                self._sv_uptime.set(f"{hh:02d}:{mm:02d}:{ss:02d}")

                if self.clicks % 50 == 0:
                    ui.append_log(self._log,
                                  f"  ✓ {self.clicks} clicks  ({avg_cps:.1f} CPS)")

                time.sleep(max(0, interval - 0.01))
            except Exception as e:
                ui.append_log(self._log, f"  ⚠ {e}"); time.sleep(0.5)

    def _quit(self): self.running = False; self.root.destroy()
    def run(self):   self.root.mainloop()

if __name__ == "__main__": AltClickMacro().run()
