"""
XZX Fishing Macro — black-and-white detector style
Auto-cast · reel · keep.
"""
import tkinter as tk
import tkinter.messagebox
import threading, time, os, sys, ctypes, ctypes.wintypes
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

WIN_W, WIN_H = 440, 540

PUL = ctypes.POINTER(ctypes.c_ulong)

class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk",ctypes.c_ushort),("wScan",ctypes.c_ushort),
                ("dwFlags",ctypes.c_ulong),("time",ctypes.c_ulong),
                ("dwExtraInfo",PUL)]

class MouseInput(ctypes.Structure):
    _fields_ = [("dx",ctypes.c_long),("dy",ctypes.c_long),
                ("mouseData",ctypes.c_ulong),("dwFlags",ctypes.c_ulong),
                ("time",ctypes.c_ulong),("dwExtraInfo",PUL)]

class Input_I(ctypes.Union):
    _fields_ = [("ki",KeyBdInput),("mi",MouseInput)]

class Input(ctypes.Structure):
    _fields_ = [("type",ctypes.c_ulong),("ii",Input_I)]

def _send_key(vk, up=False):
    try:
        inp = Input(type=1); inp.ii.ki.wVk=vk; inp.ii.ki.dwFlags=2 if up else 0
        ctypes.windll.user32.SendInput(1,ctypes.byref(inp),ctypes.sizeof(inp))
    except Exception: pass

def _lmb():
    try:
        ctypes.windll.user32.mouse_event(2,0,0,0,0)
        time.sleep(0.07)
        ctypes.windll.user32.mouse_event(4,0,0,0,0)
    except Exception: pass

try:
    import mss, numpy as np
    _HAS_CAP = True
except ImportError:
    _HAS_CAP = False

def _sample_region(x, y, w, h):
    if not _HAS_CAP: return None
    with mss.mss() as sct:
        mon = {"top":y,"left":x,"width":w,"height":h}
        img = sct.grab(mon)
        return np.frombuffer(img.rgb, dtype=np.uint8).reshape((h,w,3))

def _bite_detected(x, y, w, h, threshold=40):
    arr = _sample_region(x, y, w, h)
    if arr is None: return False
    r,g,b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    bright = (r.astype(int)+g+b)/3
    return float(bright.max()) > threshold+180


class FishingMacro(ui.Draggable):
    def __init__(self):
        self.running=False; self.casts=0; self._thread=None
        self.root=tk.Tk()
        self.root.geometry(f"{WIN_W}x{WIN_H}"); self.root.resizable(False,False)
        self._center()
        ui.frameless(self.root, WIN_W, WIN_H, radius=20)
        self._build()
        self.root.bind("<F5>",lambda e:self._start())
        self.root.bind("<F6>",lambda e:self._stop())
        self.root.protocol("WM_DELETE_WINDOW",self._quit)

    def _center(self):
        self.root.update_idletasks()
        sw,sh=self.root.winfo_screenwidth(),self.root.winfo_screenheight()
        self.root.geometry(f"{WIN_W}x{WIN_H}+{(sw-WIN_W)//2}+{(sh-WIN_H)//2}")

    def _build(self):
        bgcv=tk.Canvas(self.root,width=WIN_W,height=WIN_H,highlightthickness=0,bg=ui.BG)
        bgcv.place(x=0,y=0)
        bgcv.configure(bg=ui.BG)   # flat black — matches the launcher/Hub theme now
        self.badge=ui.macro_header(self,WIN_W,"🎣","FISHING MACRO",ui.CYAN)
        tk.Frame(self.root,bg=ui.CYAN,height=2).place(x=0,y=ui.HEADER_H,width=WIN_W)
        sb=tk.Frame(self.root,bg=ui.PANEL2,highlightbackground=ui.BORDER,highlightthickness=1)
        sb.place(x=0,y=ui.HEADER_H+2,width=ui.SIDEBAR_W,height=WIN_H-ui.HEADER_H-2)
        self._stabs=[]
        for i,(ico,name) in enumerate([("🎣","Macro"),("📊","Stats"),("⚙","Settings")]):
            btn=ui.SideIconBtn(sb,ico,name,active_color=ui.CYAN)
            btn.frame.pack(fill="x")
            btn.bind("<Button-1>",lambda e,idx=i:self._tab(idx))
            self._stabs.append(btn)
        cx=ui.SIDEBAR_W+1; cw=WIN_W-cx; ch=WIN_H-ui.HEADER_H-2
        self.cont=tk.Frame(self.root,bg=ui.PANEL)
        self.cont.place(x=cx,y=ui.HEADER_H+2,width=cw,height=ch)
        v0=self._mk_macro_view(cw,ch); v1=self._mk_stats_view(cw,ch); v2=self._mk_settings_view(cw,ch)
        self._views=[v0,v1,v2]; self._tab(0)

    def _mk_macro_view(self,w,h):
        f=tk.Frame(self.cont,bg=ui.PANEL,width=w,height=h); f.pack_propagate(False)
        body=tk.Frame(f,bg=ui.PANEL); body.pack(fill="both",expand=True,padx=20,pady=16)
        tk.Label(body,text="🎣",bg=ui.PANEL,fg=ui.CYAN,font=("Segoe UI Emoji",36)).pack(pady=(8,4))
        tk.Label(body,text="Fishing Auto-Macro",bg=ui.PANEL,fg=ui.FG,font=("Segoe UI Black",14)).pack()
        tk.Label(body,text="Detects bite flash and auto-reels",bg=ui.PANEL,fg=ui.MUTED,font=("Segoe UI",9)).pack(pady=(2,16))
        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x")
        tiles=tk.Frame(body,bg=ui.PANEL); tiles.pack(fill="x",pady=12)
        self._t_casts=ui.StatTile(tiles,"CASTS","0",accent=ui.CYAN)
        self._t_casts.frame.pack(side="left",expand=True,fill="both",padx=(0,4))
        self._t_leg=ui.StatTile(tiles,"LEGENDARY","0",accent=ui.ACCENT)
        self._t_leg.frame.pack(side="left",expand=True,fill="both",padx=(4,0))
        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x",pady=(0,12))
        self._toggle=ui.ToggleButton(body,self._on_toggle,start_bg=ui.CYAN,
                                      start_label="▶  START FISHING",stop_label="■  STOP FISHING",stop_bg=ui.RED)
        self._toggle.btn.pack(fill="x",ipady=4); self._toggle.btn.configure(fg=ui.BG)
        hr=tk.Frame(body,bg=ui.PANEL); hr.pack(fill="x",pady=(10,0))
        for k,txt in [("F5","Start"),("F6","Stop")]:
            kg=tk.Frame(hr,bg=ui.PANEL); kg.pack(side="left",padx=(0,6))
            tk.Label(kg,text=k,bg=ui.PANEL2,fg=ui.FG2,font=("Consolas",8),padx=6,pady=3,
                     highlightbackground=ui.BORDER2,highlightthickness=1).pack(side="left")
            tk.Label(kg,text=txt,bg=ui.PANEL,fg=ui.MUTED,font=("Segoe UI",8)).pack(side="left",padx=3)
        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x",pady=(10,4))
        self._log,sb2=ui.log_box(body,height=5)
        sb2.pack(side="right",fill="y"); self._log.pack(side="left",fill="both",expand=True)
        return f

    def _mk_stats_view(self,w,h):
        f=tk.Frame(self.cont,bg=ui.PANEL,width=w,height=h)
        body=tk.Frame(f,bg=ui.PANEL); body.pack(fill="both",expand=True,padx=20,pady=20)
        tk.Label(body,text="SESSION STATS",bg=ui.PANEL,fg=ui.MUTED,font=("Segoe UI Semibold",8)).pack(anchor="w")
        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x",pady=(4,12))
        def row(lbl):
            r=tk.Frame(body,bg=ui.PANEL); r.pack(fill="x",pady=4)
            tk.Label(r,text=lbl,bg=ui.PANEL,fg=ui.MUTED,font=("Segoe UI",9)).pack(side="left")
            v=tk.StringVar(value="0")
            tk.Label(r,textvariable=v,bg=ui.PANEL,fg=ui.FG,font=("Segoe UI Semibold",9)).pack(side="right")
            return v
        self._sv_casts=row("Total Casts"); self._sv_leg=row("Legendary"); self._sv_uptime=row("Uptime")
        return f

    def _mk_settings_view(self,w,h):
        f=tk.Frame(self.cont,bg=ui.PANEL,width=w,height=h)
        body=tk.Frame(f,bg=ui.PANEL); body.pack(fill="both",expand=True,padx=20,pady=20)

        ui.section_label(body,"Screen",bg=ui.PANEL)
        mon_row=tk.Frame(body,bg=ui.PANEL); mon_row.pack(fill="x",pady=5)
        tk.Label(mon_row,text="Monitor",bg=ui.PANEL,fg=ui.FG2,font=("Segoe UI",9)).pack(side="left")
        self._monitors=ui.get_monitors()
        self.v_monitor=tk.StringVar(value=self._monitors[0][0])
        ui.dropdown(mon_row,self.v_monitor,[m[0] for m in self._monitors],width=16).pack(side="right")

        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x",pady=8)
        ui.section_label(body,"Bite Region (on chosen monitor)",bg=ui.PANEL)
        def rowi(lbl,default,lo,hi):
            r=tk.Frame(body,bg=ui.PANEL); r.pack(fill="x",pady=5)
            tk.Label(r,text=lbl,bg=ui.PANEL,fg=ui.FG2,font=("Segoe UI",9)).pack(side="left")
            v=tk.IntVar(value=default); ui.spinbox(r,v,lo,hi,step=5,width=7).pack(side="right"); return v
        self.v_scan_x=rowi("Region X",850,0,3840)
        self.v_scan_y=rowi("Region Y",400,0,2160)
        self.v_scan_w=rowi("Region width",220,20,800)
        self.v_scan_h=rowi("Region height",100,20,800)

        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x",pady=8)
        ui.section_label(body,"Timing",bg=ui.PANEL)
        def row(lbl,default,lo,hi):
            r=tk.Frame(body,bg=ui.PANEL); r.pack(fill="x",pady=5)
            tk.Label(r,text=lbl,bg=ui.PANEL,fg=ui.FG2,font=("Segoe UI",9)).pack(side="left")
            v=tk.DoubleVar(value=default); ui.spinbox(r,v,lo,hi,step=0.1,width=6).pack(side="right"); return v
        self.v_cast_delay=row("Cast delay (s)",1.5,0.5,5.0)
        self.v_reel_delay=row("Reel delay (s)",0.3,0.1,2.0)
        self.v_check_int=row("Check interval (s)",0.15,0.05,1.0)
        tk.Frame(body,bg=ui.BORDER2,height=1).pack(fill="x",pady=8)
        ui.section_label(body,"Detection",bg=ui.PANEL)
        dr=tk.Frame(body,bg=ui.PANEL); dr.pack(fill="x",pady=5)
        tk.Label(dr,text="Bite threshold",bg=ui.PANEL,fg=ui.FG2,font=("Segoe UI",9)).pack(side="left")
        self.v_threshold=tk.IntVar(value=40); ui.spinbox(dr,self.v_threshold,10,120,step=5,width=6).pack(side="right")
        return f

    def _monitor_offset(self):
        label=self.v_monitor.get()
        for name,left,top,mw,mh in self._monitors:
            if name==label: return left,top
        return 0,0

    def _tab(self,idx):
        for i,btn in enumerate(self._stabs): btn.set_active(i==idx)
        for i,v in enumerate(self._views):
            if i==idx: v.place(x=0,y=0,relwidth=1,relheight=1); v.lift()
            else: v.place_forget()

    def _on_toggle(self,running):
        if running: self._start()
        else: self._stop()

    def _start(self):
        if self.running: return
        self.running=True; self._start_ts=time.time()
        self.badge.set("Running",ui.GREEN)
        self._toggle.btn.configure(text="■  STOP FISHING",bg=ui.RED,fg="white")
        self._thread=threading.Thread(target=self._loop,daemon=True); self._thread.start()
        ui.append_log(self._log,"▶ Fishing started")

    def _stop(self):
        if not self.running: return
        self.running=False; self.badge.set("Stopped",ui.MUTED)
        self._toggle.force_stop(); ui.append_log(self._log,"■ Fishing stopped")

    def _loop(self):
        while self.running:
            try:
                cast_delay=self.v_cast_delay.get(); reel_delay=self.v_reel_delay.get()
                check_int=self.v_check_int.get(); threshold=self.v_threshold.get()
                _lmb(); self.casts+=1; self._t_casts.set(self.casts); self._sv_casts.set(str(self.casts))
                ui.append_log(self._log,f"🎣 Cast #{self.casts}"); time.sleep(cast_delay)
                mon_x,mon_y=self._monitor_offset()
                region=(mon_x+self.v_scan_x.get(), mon_y+self.v_scan_y.get(),
                        self.v_scan_w.get(), self.v_scan_h.get())
                waited=0.0
                while self.running and waited<30:
                    if _bite_detected(*region, threshold=threshold):
                        ui.append_log(self._log,"  ✓ Bite! Reeling…")
                        time.sleep(reel_delay); _lmb(); time.sleep(0.4); break
                    time.sleep(check_int); waited+=check_int
                    e=time.time()-self._start_ts
                    self._sv_uptime.set(f"{int(e//3600):02d}:{int((e%3600)//60):02d}:{int(e%60):02d}")
            except Exception as ex:
                ui.append_log(self._log,f"  ⚠ {ex}"); time.sleep(1.0)

    def _quit(self): self.running=False; self.root.destroy()
    def run(self): self.root.mainloop()

if __name__=="__main__": FishingMacro().run()
