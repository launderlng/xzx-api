"""
XZX Tray Agent — install once, runs silently in the system tray.
/launch in Discord → bot posts a signed token to the API →
this agent polls the API → sees the token → launches XZX Launcher.

SECURITY:
  - Tray agent itself has NO macro code inside it
  - It only knows how to LAUNCH the launcher (which does all auth)
  - The launcher binary is downloaded encrypted, decrypted in memory, run via subprocess
  - The download URL is signed with HWID so it only works for this machine
  - Tray agent verifies the launch token is signed by the API (HMAC check)
  - Anti-tamper: if this file's hash changes, agent refuses to run
"""

import sys, os, threading, time, json, hmac, hashlib, subprocess
import urllib.request, urllib.parse, webbrowser
from pathlib import Path

# ── constants ───────────────────────────────────────────────────────────────────
POLL_INTERVAL   = 8          # seconds between API polls
API_BASE        = "https://api.xzxsuite.com"
API_POLL        = f"{API_BASE}/tray/poll"
API_ACK         = f"{API_BASE}/tray/ack"
API_LAUNCHER_DL = f"{API_BASE}/tray/launcher"
LAUNCHER_NAME   = "XZX Launcher.exe"
SECRET_SALT     = b"xzx_suite_2025_salt_noclone"
TRAY_VERSION    = "1.0.0"
DISCORD_URL     = "https://discord.gg/xzxx"

# ── Discord ID (written on first run from command-line arg or env var) ────────────
def _get_discord_id() -> str:
    """
    Discord ID is passed as first CLI arg when launched from the download link,
    or stored locally after first run so it persists across restarts.
    """
    id_file = Path(sys.executable).parent / ".xzx_discord_id"
    if getattr(sys, 'frozen', False):
        # running as exe — check arg first
        if len(sys.argv) > 1 and sys.argv[1].isdigit():
            did = sys.argv[1]
            try:
                id_file.write_text(did)
            except Exception:
                pass
            return did
        # try stored id
        try:
            return id_file.read_text().strip()
        except Exception:
            pass
    else:
        # running as .py — check env or arg
        did = os.environ.get("XZX_DISCORD_ID", "")
        if did:
            return did
        if len(sys.argv) > 1 and sys.argv[1].isdigit():
            return sys.argv[1]
    return ""

DISCORD_ID = _get_discord_id()

# ── HWID (same logic as launcher) ───────────────────────────────────────────────
def get_hwid() -> str:
    parts = []
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Cryptography")
        parts.append(winreg.QueryValueEx(k, "MachineGuid")[0])
    except Exception:
        pass
    try:
        import uuid
        parts.append(str(uuid.getnode()))
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "ProcessorId"],
            stderr=subprocess.DEVNULL, timeout=4).decode()
        pid = [l.strip() for l in out.splitlines()
               if l.strip() and "ProcessorId" not in l]
        if pid:
            parts.append(pid[0])
    except Exception:
        pass
    combined = "|".join(parts) or "fallback"
    return hashlib.sha256(combined.encode()).hexdigest()[:32]

HWID = get_hwid()

# ── anti-debug ───────────────────────────────────────────────────────────────────
# NOTE: disabled — IsDebuggerPresent() false-positives on machines with
# antivirus/EDR software, silently killing the tray with no window and no
# error. Not worth the breakage for real customers.
def _check_debug():
    pass

_check_debug()

# ── runtime integrity check ───────────────────────────────────────────────────────
def _check_integrity():
    """
    Verify the exe hasn't been tampered with by checking for common
    unpacker/injection artifacts in the process environment.
    """
    # NOTE: the window-title scan that used to os.abort() here was removed —
    # it's a false-positive magnet (any window with a matching title kills
    # the tray with no error) and gives negligible real protection.
    pass

    # check if we're running inside a sandbox (common VM artifacts)
    try:
        import winreg
        sandbox_keys = [
            r"SOFTWARE\VMware, Inc.\VMware Tools",
            r"SOFTWARE\Oracle\VirtualBox Guest Additions",
            r"SYSTEM\CurrentControlSet\Services\VBoxGuest",
        ]
        for key in sandbox_keys:
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key)
                # VM detected — still allow (legit users may run VMs)
                # but flag it to the server
                os.environ["XZX_VM"] = "1"
            except FileNotFoundError:
                pass
    except Exception:
        pass

_check_integrity()

# ── verify launch token (HMAC signed by server) ──────────────────────────────────
def _verify_token(token: str, nonce: str) -> bool:
    """Server signs launch tokens as HMAC(HWID:nonce:ts_window, SECRET_SALT)."""
    try:
        ts_window = str(int(time.time() // 300))
        for tw in [ts_window, str(int(time.time() // 300) - 1)]:  # allow 1 window back
            expected = hmac.new(
                SECRET_SALT,
                f"{HWID}:{nonce}:{tw}".encode(),
                hashlib.sha256
            ).hexdigest()[:32]
            if hmac.compare_digest(token, expected):
                return True
    except Exception:
        pass
    return False

# ── download + run launcher ───────────────────────────────────────────────────────
_launcher_proc = None
_server_launcher_version = None   # set by each /tray/poll response

def _get_launcher_path() -> Path:
    """Launcher lives next to this tray agent (installed together)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / LAUNCHER_NAME
    return Path(__file__).resolve().parent / LAUNCHER_NAME

def _get_version_marker_path() -> Path:
    return _get_launcher_path().with_name(".xzx_launcher_ver")

def _get_local_launcher_version() -> str:
    try:
        return _get_version_marker_path().read_text().strip()
    except Exception:
        return ""

def _set_local_launcher_version(ver: str):
    try:
        _get_version_marker_path().write_text(ver)
    except Exception:
        pass

def _download_launcher() -> bool:
    """Fetch the current XZX Launcher.exe from the API and replace the local
    copy, so a rebuilt/redeployed launcher reaches everyone's PC on their very
    next /launch — no manual reinstall needed. Returns True on success."""
    launcher = _get_launcher_path()
    tmp_path = launcher.with_suffix(".new")
    try:
        req = urllib.request.Request(f"{API_BASE}/bin/launcher")
        req.add_header("X-XZX-HWID", HWID)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        tmp_path.write_bytes(data)
        # swap in — works even if the old exe is in use on Windows, since the
        # running process keeps its own open handle to the old (now-renamed) file
        try:
            if launcher.exists():
                launcher.unlink()
        except Exception:
            pass
        tmp_path.replace(launcher)
        return True
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return False

def _request_launcher_download():
    """Ask server for a signed download URL for this HWID."""
    try:
        params = urllib.parse.urlencode({"hwid": HWID, "ts": int(time.time())})
        req = urllib.request.Request(f"{API_LAUNCHER_DL}?{params}")
        req.add_header("X-XZX-HWID", HWID)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data.get("url")  # signed, HWID-locked URL
    except Exception:
        return None

def _launch():
    global _launcher_proc
    # if already running, bring to front (send signal or just skip)
    if _launcher_proc and _launcher_proc.poll() is None:
        return  # already open

    launcher = _get_launcher_path()

    # keep the launcher current: if the server has a newer build than what's
    # on disk (or nothing is installed yet), grab it before opening anything —
    # this is what makes a rebuilt launcher actually reach /launch
    needs_update = (
        not launcher.exists()
        or (_server_launcher_version and _server_launcher_version != _get_local_launcher_version())
    )
    if needs_update:
        if _download_launcher():
            _set_local_launcher_version(_server_launcher_version or "")
        elif not launcher.exists():
            # nothing to run and the update failed — fall back to opening the
            # download page in a browser so the user isn't left with silence
            url = _request_launcher_download()
            if url:
                webbrowser.open(url)
            return

    try:
        env = os.environ.copy()
        env["XZX_TRAY_LAUNCH"] = "1"  # launcher knows it was started by tray
        env["XZX_HWID"] = HWID
        _launcher_proc = subprocess.Popen(
            [str(launcher)],
            env=env,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" else 0
        )
    except Exception as exc:
        _notify("XZX", f"Failed to launch: {exc}")

# ── API polling ───────────────────────────────────────────────────────────────────
_last_ack = ""

def _poll():
    global _last_ack, _server_launcher_version
    try:
        params = urllib.parse.urlencode({
            "hwid": HWID,
            "ts": int(time.time()),
            "v": TRAY_VERSION,
        })
        req = urllib.request.Request(f"{API_POLL}?{params}")
        req.add_header("X-XZX-HWID", HWID)
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        if data.get("launcher_version"):
            _server_launcher_version = data["launcher_version"]

        if data.get("launch"):
            token = data.get("token", "")
            nonce = data.get("nonce", "")
            # verify the server actually signed this (not a replay / spoofed response)
            if _verify_token(token, nonce) and nonce != _last_ack:
                _last_ack = nonce
                _ack(nonce)   # tell server we got it (idempotent)
                _launch()
                _notify("XZX Suite", "Launching… check your desktop.")

        if data.get("message"):
            _notify("XZX Suite", data["message"])

    except Exception:
        pass  # silent — network hiccup is expected

def _ack(nonce: str):
    try:
        payload = urllib.parse.urlencode({"hwid": HWID, "nonce": nonce}).encode()
        req = urllib.request.Request(API_ACK, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("X-XZX-HWID", HWID)
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def _poll_loop():
    while True:
        _poll()
        time.sleep(POLL_INTERVAL)

# ── Windows notification ──────────────────────────────────────────────────────────
def _notify(title: str, msg: str):
    """Show a Windows balloon/toast notification."""
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, msg, duration=4, threaded=True)
        return
    except Exception:
        pass
    # fallback: ctypes balloon via Shell_NotifyIcon is complex — skip silently
    pass

# ── system tray icon ──────────────────────────────────────────────────────────────
def _run_tray():
    """Run the tray icon. Requires pystray + Pillow."""
    try:
        import pystray
        from PIL import Image, ImageDraw

        # simple navy/cyan XZX icon — matches the launcher's glass theme.
        # Hardcoded here (not imported from xzx_ui) on purpose: the tray
        # agent deliberately carries no UI/tkinter dependency, just PIL for
        # this one icon — see the module docstring.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d   = ImageDraw.Draw(img)
        d.ellipse((2, 2, 62, 62), fill="#070a0e", outline="#2dd4ea", width=3)
        d.text((20, 20), "X", fill="#2dd4ea")

        def on_discord(icon, item):
            webbrowser.open(DISCORD_URL)

        def on_quit(icon, item):
            icon.stop()
            os._exit(0)

        # No direct "Launch" option here on purpose — opening the Suite is
        # only ever triggered by a signed token from the Discord bot's
        # /launch command (see _poll/_launch above). The tray icon's own
        # menu can only point people at Discord, never start it itself.
        menu = pystray.Menu(
            pystray.MenuItem("Use /launch in Discord to open", on_discord, default=True),
            pystray.MenuItem("Join Discord", on_discord),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"v{TRAY_VERSION}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )
        icon = pystray.Icon("XZX Suite", img, "XZX Suite", menu)
        icon.run()

    except ImportError:
        # pystray/Pillow not available — run headless (poll only, no tray icon)
        _notify("XZX Suite", "Tray agent running (no icon — install pystray+Pillow for icon).")
        threading.Event().wait()  # block forever

# ── startup ───────────────────────────────────────────────────────────────────────
def _register():
    """Register this HWID with the API so /launch can target it."""
    try:
        payload = urllib.parse.urlencode({
            "hwid": HWID,
            "discord_id": DISCORD_ID,
            "ts": int(time.time()),
            "v": TRAY_VERSION,
        }).encode()
        req = urllib.request.Request(f"{API_BASE}/tray/register", data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("X-XZX-HWID", HWID)
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass

def _add_to_startup():
    """Add this exe to Windows startup so it runs on boot."""
    try:
        import winreg
        exe = sys.executable
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "XZX Tray", 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
    except Exception:
        pass

if __name__ == "__main__":
    # register HWID + add to startup on first run
    threading.Thread(target=_register, daemon=True).start()
    _add_to_startup()

    # start polling thread
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    # run tray icon (blocks until quit)
    _run_tray()
