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

WIN_W, WIN_H = 440, 520

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
class AltClickMacro:
    def __init__(self):
        self.running = False
        self.clicks  = 0
        self._thread = None

        self.root = tk.Tk()
        self.root.title("XZX — Alt Click")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=ui.BG)
        self._center()
        self._build()
        self.root.bind("<F9>", lambda e: self._toggle())
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    def _center(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{WIN_W}x{WIN_H}+{(sw-WIN_W)//2}+{(sh-WIN_H)//2}")

    def _build(self):
        bgcv = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                         highlightthickness=0, bg=ui.BG)
        bgcv.place(x=0, y=0)
        bgcv.configure(bg=ui.BG)   # flat black — matches the launcher/Hub theme now

        # header
        hdr = tk.Frame(self.root, bg=ui.PANEL2,
                       highlightbackground=ui.BORDER, highlightthickness=1)
        hdr.place(x=0, y=0, width=WIN_W, height=ui.HEADER_H)
        tk.Label(hdr, text="🖱️", bg=ui.PANEL2, fg=ui.GREEN,
                 font=("Segoe UI Emoji", 16)).pack(side="left", padx=(14,4))
        tk.Label(hdr, text="ALT CLICK MACRO", bg=ui.PANEL2, fg=ui.FG,
                 font=("Segoe UI Black", 12)).pack(side="left")
        self.badge = ui.StatusBadge(hdr, bg=ui.PANEL2)
        self.badge.frame.pack(side="right", padx=14)

        tk.Frame(self.root, bg=ui.GREEN, height=2).place(
            x=0, y=ui.HEADER_H, width=WIN_W)

        # sidebar
        sb = tk.Frame(self.root, bg=ui.PANEL2,
                      highlightbackground=ui.BORDER, highlightthickness=1)
        sb.place(x=0, y=ui.HEADER_H+2,
                 width=ui.SIDEBAR_W, height=WIN_H-ui.HEADER_H-2)

        self._stabs = []
        for i,(ico,name) in enumerate([("🖱️","Macro"),("📊","Stats"),("⚙","Settings")]):
            btn = ui.SideIconBtn(sb, ico, name, active_color=ui.GREEN)
            btn.frame.pack(fill="x")
            btn.bind("<Button-1>", lambda e, idx=i: self._tab(idx))
            self._stabs.append(btn)

        cx = ui.SIDEBAR_W+1
        cw = WIN_W - cx
        ch = WIN_H - ui.HEADER_H - 2
        self.cont = tk.Frame(self.root, bg=ui.PANEL)
        self.cont.place(x=cx, y=ui.HEADER_H+2, width=cw, height=ch)

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

        tk.Label(body, text="🖱️", bg=ui.PANEL, fg=ui.GREEN,
                 font=("Segoe UI Emoji", 36)).pack(pady=(8,4))
        tk.Label(body, text="Alt-Click Macro", bg=ui.PANEL, fg=ui.FG,
                 font=("Segoe UI Black", 14)).pack()
        tk.Label(body, text="Auto alt-click with configurable CPS",
                 bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI", 9)).pack(pady=(2,16))

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x")

        tiles = tk.Frame(body, bg=ui.PANEL)
        tiles.pack(fill="x", pady=12)
        self._t_clicks = ui.StatTile(tiles, "CLICKS", "0", accent=ui.GREEN)
        self._t_clicks.frame.pack(side="left", expand=True, fill="both", padx=(0,4))
        self._t_cps = ui.StatTile(tiles, "CPS", "0.0", accent=ui.CYAN)
        self._t_cps.frame.pack(side="left", expand=True, fill="both", padx=(4,0))

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(0,12))

        self._tbtn = ui.ToggleButton(body, self._on_toggle,
                                      start_label="▶  START CLICKING",
                                      stop_label="■  STOP CLICKING",
                                      start_bg=ui.GREEN2, stop_bg=ui.RED)
        self._tbtn.btn.pack(fill="x", ipady=4)
        self._tbtn.btn.configure(fg=ui.BG)

        hr = tk.Frame(body, bg=ui.PANEL)
        hr.pack(fill="x", pady=(10,0))
        kg = tk.Frame(hr, bg=ui.PANEL); kg.pack(side="left", padx=(0,6))
        tk.Label(kg, text="F9", bg=ui.PANEL2, fg=ui.FG2,
                 font=("Consolas",8), padx=6, pady=3,
                 highlightbackground=ui.BORDER2,
                 highlightthickness=1).pack(side="left")
        tk.Label(kg, text="Toggle", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI",8)).pack(side="left", padx=3)

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(10,4))
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
        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=(4,12))

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
        ui.section_label(body, "Click Timing", bg=ui.PANEL)

        def row(lbl, default, lo, hi, step):
            r = tk.Frame(body, bg=ui.PANEL); r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=ui.PANEL, fg=ui.FG2,
                     font=("Segoe UI",9)).pack(side="left")
            v = tk.DoubleVar(value=default)
            ui.spinbox(r, v, lo, hi, step=step, width=7).pack(side="right")
            return v

        self.v_cps     = row("CPS (clicks/sec)",   8.0, 1.0, 30.0, 0.5)
        self.v_slide   = row("Slide distance (px)", 5,   1,   30,   1)
        self.v_hold_ms = row("Hold duration (ms)",  80,  10,  300,  10)

        tk.Frame(body, bg=ui.BORDER2, height=1).pack(fill="x", pady=8)
        ui.section_label(body, "Behaviour", bg=ui.PANEL)
        self.v_jitter = tk.BooleanVar(value=True)
        jr = tk.Frame(body, bg=ui.PANEL); jr.pack(fill="x", pady=5)
        tk.Label(jr, text="Random jitter", bg=ui.PANEL, fg=ui.FG2,
                 font=("Segoe UI",9)).pack(side="left")
        tk.Checkbutton(jr, variable=self.v_jitter, bg=ui.PANEL,
                       selectcolor=ui.PANEL2,
                       activebackground=ui.PANEL, relief="flat",
                       bd=0).pack(side="right")
        return f

    def _tab(self, idx):
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
