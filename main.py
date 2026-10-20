"""
XZX API Server
Handles license verification, tray agent signalling, and OAuth.
Deploy on Railway as a separate service from the bot.
Set the custom domain to api.xzxsuite.com in Railway settings.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
import uvicorn

# ── config ───────────────────────────────────────────────────────────────────────
SECRET_SALT  = b"xzx_suite_2025_salt_noclone"
DB_PATH      = os.getenv("API_DB_PATH", "xzx_api.db")
TRAY_EXE_URL = os.getenv("TRAY_EXE_URL", "")   # set in Railway: direct link to XZX Tray.exe
SIGNAL_TTL   = 60  # seconds a launch signal stays valid
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")   # set in Railway to enable /admin/grant

# ── database ─────────────────────────────────────────────────────────────────────
def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = get_db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS tray_agents (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id  TEXT NOT NULL,
        hwid        TEXT NOT NULL,
        version     TEXT,
        last_seen   TEXT NOT NULL,
        UNIQUE(discord_id, hwid)
    );
    CREATE INDEX IF NOT EXISTS idx_tray_discord ON tray_agents (discord_id, last_seen);

    CREATE TABLE IF NOT EXISTS tray_signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        hwid        TEXT NOT NULL,
        discord_id  TEXT NOT NULL,
        nonce       TEXT NOT NULL UNIQUE,
        token       TEXT NOT NULL,
        created_at  REAL NOT NULL,
        acked       INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS licenses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id  TEXT NOT NULL,
        hwid        TEXT,
        active      INTEGER NOT NULL DEFAULT 1,
        macro       TEXT,
        expires_at  TEXT,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_lic_discord ON licenses (discord_id, active);
    """)
    con.commit()
    con.close()

# ── helpers ───────────────────────────────────────────────────────────────────────
def _make_launch_token(hwid: str, nonce: str) -> str:
    ts_window = str(int(time.time() // 300))
    raw = f"{hwid}:{nonce}:{ts_window}".encode()
    return hmac.new(SECRET_SALT, raw, hashlib.sha256).hexdigest()[:32]

def _verify_launch_token(token: str, hwid: str, nonce: str) -> bool:
    ts = int(time.time() // 300)
    for tw in [str(ts), str(ts - 1)]:
        expected = hmac.new(
            SECRET_SALT,
            f"{hwid}:{nonce}:{tw}".encode(),
            hashlib.sha256
        ).hexdigest()[:32]
        if hmac.compare_digest(token, expected):
            return True
    return False

def _has_valid_license(discord_id: str) -> bool:
    con = get_db()
    row = con.execute(
        """SELECT 1 FROM licenses
           WHERE discord_id = ?
             AND active = 1
             AND (expires_at IS NULL OR expires_at > datetime('now'))
           LIMIT 1""",
        (str(discord_id),)
    ).fetchone()
    con.close()
    return row is not None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ── app ───────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

# ─────────────────────────────────────────────────────────────────────────────────
#  TRAY ROUTES
# ─────────────────────────────────────────────────────────────────────────────────

@app.get("/tray/hwid")
async def tray_hwid(discord_id: str = Query(default="")):
    """Bot calls this to look up the HWID registered for a Discord user."""
    if not discord_id:
        raise HTTPException(400, "missing discord_id")
    con = get_db()
    row = con.execute(
        "SELECT hwid FROM tray_agents WHERE discord_id = ? ORDER BY last_seen DESC LIMIT 1",
        (str(discord_id),)
    ).fetchone()
    con.close()
    return {"hwid": row[0] if row else None}


@app.post("/tray/register")
async def tray_register(request: Request):
    """Tray agent calls this on startup to link HWID → Discord ID."""
    form = await request.form()
    discord_id = str(form.get("discord_id", "")).strip()
    hwid       = str(form.get("hwid", "")).strip()
    version    = str(form.get("v", "")).strip()

    if not discord_id or not hwid:
        raise HTTPException(400, "missing discord_id or hwid")

    con = get_db()
    con.execute(
        """INSERT INTO tray_agents (discord_id, hwid, version, last_seen)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(discord_id, hwid) DO UPDATE SET
               version   = excluded.version,
               last_seen = excluded.last_seen""",
        (discord_id, hwid, version, now_iso())
    )
    con.commit()
    con.close()
    return {"ok": True}


@app.get("/tray/poll")
async def tray_poll(
    hwid: str = Query(...),
    discord_id: str = Query(default=""),
    v: str = Query(default=""),
):
    """Tray agent polls this every 8s. Returns a launch signal if one is pending."""
    if not hwid:
        raise HTTPException(400, "missing hwid")

    # update last_seen if we know the discord_id
    if discord_id:
        con = get_db()
        con.execute(
            "UPDATE tray_agents SET last_seen = ? WHERE hwid = ?",
            (now_iso(), hwid)
        )
        con.commit()
        con.close()

    # look for a pending, unacked signal within TTL
    con = get_db()
    row = con.execute(
        """SELECT nonce, token FROM tray_signals
           WHERE hwid = ?
             AND acked = 0
             AND created_at > ?
           ORDER BY created_at DESC LIMIT 1""",
        (hwid, time.time() - SIGNAL_TTL)
    ).fetchone()
    con.close()

    if row:
        return {"launch": True, "nonce": row["nonce"], "token": row["token"]}
    return {"launch": False}


@app.post("/tray/signal")
async def tray_signal(request: Request):
    """Bot calls this when a user runs /launch. Stores a signed token for their HWID."""
    form = await request.form()
    hwid       = str(form.get("hwid", "")).strip()
    discord_id = str(form.get("discord_id", "")).strip()
    token      = str(form.get("token", "")).strip()
    nonce      = str(form.get("nonce", "")).strip()

    if not hwid or not discord_id or not token or not nonce:
        raise HTTPException(400, "missing fields")

    # verify the bot sent a valid token (not spoofed)
    if not _verify_launch_token(token, hwid, nonce):
        raise HTTPException(403, "invalid token")

    # store it — tray will pick it up on next poll
    con = get_db()
    try:
        con.execute(
            """INSERT OR IGNORE INTO tray_signals
               (hwid, discord_id, nonce, token, created_at, acked)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (hwid, discord_id, nonce, token, time.time())
        )
        con.commit()
    finally:
        con.close()

    return {"ok": True}


@app.post("/tray/ack")
async def tray_ack(request: Request):
    """Tray agent calls this after opening the launcher to mark signal as done."""
    form = await request.form()
    hwid  = str(form.get("hwid", "")).strip()
    nonce = str(form.get("nonce", "")).strip()

    if not hwid or not nonce:
        raise HTTPException(400, "missing fields")

    con = get_db()
    con.execute(
        "UPDATE tray_signals SET acked = 1 WHERE hwid = ? AND nonce = ?",
        (hwid, nonce)
    )
    con.commit()
    con.close()
    return {"ok": True}


BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")

@app.get("/tray/download")
async def tray_download(discord_id: str = Query(default="")):
    """Returns the download URL for XZX Tray.exe."""
    return {"url": f"{os.getenv('API_PUBLIC_URL', 'https://api.xzxsuite.com')}/bin/tray"}


@app.get("/bin/tray")
async def bin_tray():
    path = os.path.join(BIN_DIR, "XZX Tray.exe")
    if not os.path.exists(path):
        raise HTTPException(404, "not built yet")
    return FileResponse(path, media_type="application/octet-stream",
                         filename="XZX Tray.exe")


@app.get("/bin/launcher")
async def bin_launcher():
    path = os.path.join(BIN_DIR, "XZX Launcher.exe")
    if not os.path.exists(path):
        raise HTTPException(404, "not built yet")
    return FileResponse(path, media_type="application/octet-stream",
                         filename="XZX Launcher.exe")


@app.get("/bin/tray-src")
async def bin_tray_src():
    """Raw xzx_tray.py source — lets a dev grab the latest fix without
    waiting on a fresh exe build."""
    path = os.path.join(BIN_DIR, "xzx_tray.py")
    if not os.path.exists(path):
        raise HTTPException(404, "not uploaded yet")
    return FileResponse(path, media_type="text/x-python",
                         filename="xzx_tray.py")


@app.get("/tray/install")
async def tray_install(discord_id: str = Query(default="")):
    """
    Serves a one-click .bat installer that:
    1. Downloads XZX Tray.exe and XZX Launcher.exe
    2. Saves the user's Discord ID next to them
    3. Registers the tray as a Windows startup app (so it survives reboots)
    4. Runs the tray
    5. Deletes its own installer .bat file (normal installer cleanup —
       nothing else on the user's PC is touched or deleted)
    """
    if not discord_id:
        raise HTTPException(400, "missing discord_id")

    base = os.getenv("API_PUBLIC_URL", "https://api.xzxsuite.com")

    bat = f"""@echo off
title XZX Suite Setup
echo ============================================
echo  XZX Suite - One-Time Setup
echo ============================================
echo.

set INSTALL_DIR=%LOCALAPPDATA%\\XZX Suite
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: Save Discord ID so the tray knows who you are
echo {discord_id}> "%INSTALL_DIR%\\.xzx_discord_id"

echo Downloading XZX Tray...
powershell -Command "Invoke-WebRequest -Uri '{base}/bin/tray' -OutFile '%INSTALL_DIR%\\XZX Tray.exe'" 2>nul
if not exist "%INSTALL_DIR%\\XZX Tray.exe" (
    echo ERROR: Tray download failed. Check your internet connection.
    pause
    exit /b 1
)

echo Downloading XZX Launcher...
powershell -Command "Invoke-WebRequest -Uri '{base}/bin/launcher' -OutFile '%INSTALL_DIR%\\XZX Launcher.exe'" 2>nul
if not exist "%INSTALL_DIR%\\XZX Launcher.exe" (
    echo ERROR: Launcher download failed. Check your internet connection.
    pause
    exit /b 1
)

echo Adding XZX Tray to startup...
reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "XZX Tray" /t REG_SZ /d "\\"%INSTALL_DIR%\\XZX Tray.exe\\" {discord_id}" /f >nul

echo Setup complete! Launching XZX Tray...
start "" "%INSTALL_DIR%\\XZX Tray.exe" {discord_id}
echo.
echo XZX Tray is now running in your system tray and will start
echo automatically every time you turn on your PC.
echo Go back to Discord and click Launch again!
timeout /t 3 >nul

:: Clean up this installer file only (nothing else on the PC is touched)
(goto) 2>nul & del "%~f0"
"""

    from fastapi.responses import Response
    return Response(
        content=bat,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="XZX Setup.bat"'
        }
    )


# ─────────────────────────────────────────────────────────────────────────────────
#  VERIFY / LICENSE ROUTES  (used by the launcher)
# ─────────────────────────────────────────────────────────────────────────────────

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "1556620681083617340")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")

@app.post("/verify/oauth")
async def verify_oauth(request: Request):
    """
    Launcher's 'Login with Discord' flow lands here.
    1. Exchange the OAuth code for a Discord access token.
    2. Fetch the Discord user's id via /users/@me.
    3. Run the same license/HWID check as /verify.
    """
    import urllib.request as _ur
    import urllib.parse as _up

    form = await request.form()
    code         = str(form.get("code", "")).strip()
    redirect_uri = str(form.get("redirect_uri", "")).strip()
    hwid         = str(form.get("hwid", "")).strip()

    if not code or not redirect_uri:
        raise HTTPException(400, "missing code or redirect_uri")
    if not DISCORD_CLIENT_SECRET:
        raise HTTPException(503, "oauth not configured on server")

    # 1. exchange code for access token
    token_payload = _up.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
    }).encode()

    DISCORD_UA = "XZXSuite (https://xzxsuite.com, 1.0)"

    try:
        req = _ur.Request(
            "https://discord.com/api/oauth2/token",
            data=token_payload, method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": DISCORD_UA,
            },
        )
        with _ur.urlopen(req, timeout=10) as r:
            token_data = json.loads(r.read())
        access_token = token_data.get("access_token")
        if not access_token:
            return {"valid": False, "reason": "oauth_token_failed"}

        # 2. fetch discord user id
        req2 = _ur.Request(
            "https://discord.com/api/users/@me",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": DISCORD_UA,
            },
        )
        with _ur.urlopen(req2, timeout=10) as r2:
            user_data = json.loads(r2.read())
        discord_id = str(user_data.get("id", "")).strip()
        if not discord_id:
            return {"valid": False, "reason": "oauth_user_failed"}
    except _ur.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        return {"valid": False, "reason": f"oauth_http_{exc.code}: {body}"}
    except Exception as exc:
        return {"valid": False, "reason": f"oauth_error: {exc}"}

    # 3. run the same license/HWID logic as /verify
    con = get_db()
    row = con.execute(
        """SELECT * FROM licenses
           WHERE discord_id = ?
             AND active = 1
             AND (expires_at IS NULL OR expires_at > datetime('now'))
           ORDER BY id DESC LIMIT 1""",
        (discord_id,)
    ).fetchone()

    if not row:
        con.close()
        return {"valid": False, "reason": "no_license"}

    if not row["hwid"] and hwid:
        con.execute("UPDATE licenses SET hwid = ? WHERE id = ?", (hwid, row["id"]))
        con.commit()
    elif row["hwid"] and hwid and row["hwid"] != hwid:
        con.close()
        return {"valid": False, "reason": "hwid_mismatch"}
    con.close()

    return {
        "valid":      True,
        "discord_id": discord_id,
        "username":   discord_id,
        "linked":     True,
        "macros":     [row["macro"]] if row["macro"] else [],
    }


@app.post("/verify")
async def verify(request: Request):
    """Launcher calls this on every startup to validate the Discord ID + HWID."""
    form = await request.form()
    discord_id = str(form.get("discord_id", "")).strip()
    hwid       = str(form.get("hwid", "")).strip()

    if not discord_id:
        return {"valid": False, "reason": "missing_id"}

    con = get_db()
    row = con.execute(
        """SELECT * FROM licenses
           WHERE discord_id = ?
             AND active = 1
             AND (expires_at IS NULL OR expires_at > datetime('now'))
           ORDER BY id DESC LIMIT 1""",
        (discord_id,)
    ).fetchone()
    con.close()

    if not row:
        return {"valid": False, "reason": "no_license"}

    # bind HWID on first verify
    if not row["hwid"] and hwid:
        con = get_db()
        con.execute("UPDATE licenses SET hwid = ? WHERE id = ?", (hwid, row["id"]))
        con.commit()
        con.close()
    elif row["hwid"] and row["hwid"] != hwid:
        return {"valid": False, "reason": "hwid_mismatch"}

    return {
        "valid":    True,
        "username": discord_id,
        "linked":   True,
        "macros":   [row["macro"]] if row["macro"] else [],
    }


@app.post("/session")
async def session_heartbeat(request: Request):
    """Launcher heartbeat — confirm session is still valid."""
    form = await request.form()
    discord_id = str(form.get("discord_id", "")).strip()
    if not discord_id:
        return {"valid": False}
    return {"valid": _has_valid_license(discord_id)}


@app.post("/linked")
async def linked_check(request: Request):
    """Check if Discord ID is linked to a license."""
    form = await request.form()
    discord_id = str(form.get("discord_id", "")).strip()
    return {"linked": _has_valid_license(discord_id)}


# ─────────────────────────────────────────────────────────────────────────────────
#  ADMIN  (manual license grants — guarded by ADMIN_SECRET env var)
# ─────────────────────────────────────────────────────────────────────────────────

@app.get("/admin/grant")
async def admin_grant(discord_id: str = Query(...), secret: str = Query(...)):
    """
    One-off way to grant a license without a payment flow wired up yet.
    Visit this URL with your own secret to activate a Discord ID.
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(403, "bad secret")
    con = get_db()
    row = con.execute(
        "SELECT id FROM licenses WHERE discord_id = ? ORDER BY id DESC LIMIT 1",
        (discord_id,)
    ).fetchone()
    if row:
        con.execute("UPDATE licenses SET active = 1 WHERE id = ?", (row["id"],))
    else:
        con.execute(
            "INSERT INTO licenses (discord_id, active, created_at) VALUES (?, 1, ?)",
            (discord_id, now_iso())
        )
    con.commit()
    con.close()
    return {"ok": True, "discord_id": discord_id, "active": True}


# ─────────────────────────────────────────────────────────────────────────────────
#  HEALTH
# ─────────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def health():
    return {"status": "ok", "service": "xzx-api"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
