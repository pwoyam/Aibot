"""پنل مدیریت ربات هوش مصنوعی - FastAPI با احراز هویت"""
import asyncio
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import aiosqlite

# ایمپورت‌های داخلی
from auth import (
    require_auth, verify_credentials, create_session,
    is_valid_session, SESSION_COOKIE,
    is_login_rate_limited, record_login_attempt, get_client_ip,
)
from database import get_db, init_web_panel_tables, log_activity
from crypto import encrypt, decrypt

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_web_panel_tables()
    print("🚀 پنل وب راه‌اندازی شد")
    yield
    print("👋 پنل وب خاموش شد")


app = FastAPI(
    title="AI Bot Admin Panel",
    description="پنل مدیریت ربات هوش مصنوعی",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS فقط برای localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Mount static
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


def _mask_key(key: str) -> str:
    """مخفی کردن API key برای نمایش"""
    if not key or len(key) < 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def _is_valid_url(url: str) -> bool:
    """اعتبارسنجی base_url"""
    try:
        from urllib.parse import urlparse
        result = urlparse(url)
        return all([result.scheme in ('http', 'https'), result.netloc])
    except Exception:
        return False


def _is_valid_name(name: str) -> bool:
    """نام مدل: فقط حروف کوچک، عدد، - و _"""
    import re
    return bool(re.match(r'^[a-z0-9_-]{1,50}$', name))



# ===========================================================================
# Auth Routes
# ===========================================================================
@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>ورود به پنل</title>
        <style>
            body { font-family: system-ui; background: #0f172a; color: #fff;
                   display: flex; align-items: center; justify-content: center;
                   height: 100vh; margin: 0; }
            .box { background: #1e293b; padding: 40px; border-radius: 12px;
                   width: 340px; box-shadow: 0 10px 40px rgba(0,0,0,.5); }
            h1 { margin: 0 0 24px; font-size: 22px; text-align: center; }
            input { width: 100%; padding: 12px; margin-bottom: 14px;
                    border: 1px solid #334155; background: #0f172a; color: #fff;
                    border-radius: 8px; box-sizing: border-box; font-size: 14px; }
            button { width: 100%; padding: 12px; background: #3b82f6; color: #fff;
                     border: none; border-radius: 8px; cursor: pointer;
                     font-size: 15px; font-weight: 600; }
            button:hover { background: #2563eb; }
            .err { color: #ef4444; text-align: center; margin-bottom: 12px;
                   font-size: 13px; }
        </style>
    </head>
    <body>
        <div class="box">
            <h1>🔐 ورود به پنل مدیریت</h1>
            <div id="err" class="err"></div>
            <form onsubmit="return doLogin(event)">
                <input type="text" id="u" placeholder="نام کاربری" required autofocus>
                <input type="password" id="p" placeholder="رمز عبور" required>
                <button type="submit">ورود</button>
            </form>
        </div>
        <script>
            async function doLogin(e) {
                e.preventDefault();
                const u = document.getElementById('u').value;
                const p = document.getElementById('p').value;
                const r = await fetch('/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username: u, password: p})
                });
                if (r.ok) {
                    window.location.href = '/';
                } else {
                    document.getElementById('err').textContent = 'نام کاربری یا رمز اشتباه است';
                }
                return false;
            }
        </script>
    </body>
    </html>
    """


@app.post("/api/login")
async def login(payload: dict, request: Request):
    ip = get_client_ip(request)

    # بررسی rate limit
    limited, seconds = is_login_rate_limited(ip)
    if limited:
        raise HTTPException(
            status_code=429,
            detail=f"تلاش‌های زیاد. لطفاً {seconds} ثانیه دیگر دوباره امتحان کنید.",
        )

    username = payload.get("username", "")
    password = payload.get("password", "")

    if not verify_credentials(username, password):
        record_login_attempt(ip, success=False)
        await log_activity("login_failed", f"IP: {ip}")
        raise HTTPException(status_code=401, detail="نام کاربری یا رمز اشتباه")

    record_login_attempt(ip, success=True)
    token = create_session()
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="lax", max_age=86400,
    )
    await log_activity("login", f"ورود موفق از IP: {ip}")
    return response


@app.post("/api/logout")
async def logout(request: Request):
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


# ===========================================================================
# Dashboard
# ===========================================================================
@app.get("/api/dashboard", dependencies=[Depends(require_auth)])
async def get_dashboard():
    conn = await get_db()
    try:
        # کاربران
        async with conn.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]

        # پیام‌ها از جدول messages
        try:
            async with conn.execute("SELECT COUNT(*) FROM messages") as cur:
                total_messages = (await cur.fetchone())[0]
        except Exception:
            total_messages = 0

        # پیام‌های امروز
        try:
            async with conn.execute(
                "SELECT COUNT(*) FROM messages WHERE date(timestamp) = date('now')"
            ) as cur:
                today_messages = (await cur.fetchone())[0]
        except Exception:
            today_messages = 0

        # مدل‌های فعال
        async with conn.execute("SELECT COUNT(*) FROM ai_models WHERE is_active = 1") as cur:
            active_models = (await cur.fetchone())[0]

        return {
            "total_users": total_users,
            "total_messages": total_messages,
            "active_models": active_models,
            "today_messages": today_messages,
        }
    finally:
        await conn.close()


# ===========================================================================
# Models
# ===========================================================================
@app.get("/api/models", dependencies=[Depends(require_auth)])
async def list_models():
    conn = await get_db()
    try:
        async with conn.execute(
            "SELECT * FROM ai_models ORDER BY is_default DESC, name"
        ) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d['api_key'] = _mask_key(d.get('api_key', ''))  # ماسک!
            result.append(d)
        return result
    finally:
        await conn.close()


@app.post("/api/models", dependencies=[Depends(require_auth)])
async def create_model(payload: dict):
    required = ['name', 'display_name', 'api_key', 'base_url', 'model_name']
    for f in required:
        if not payload.get(f):
            raise HTTPException(400, f"فیلد {f} الزامی است")

    # ✅ اعتبارسنجی
    if not _is_valid_name(payload['name']):
        raise HTTPException(400, "نام باید فقط حروف کوچک، عدد، - و _ باشد (حداکثر ۵۰ کاراکتر)")
    if not _is_valid_url(payload['base_url']):
        raise HTTPException(400, "Base URL نامعتبر است (باید با http:// یا https:// شروع شود)")
    if len(payload['api_key']) < 10:
        raise HTTPException(400, "API Key خیلی کوتاه است")

    conn = await get_db()
    try:
        try:
            await conn.execute('''
                INSERT INTO ai_models (name, display_name, api_key, base_url, model_name)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                payload['name'], payload['display_name'], encrypt(payload['api_key']),
                payload['base_url'], payload['model_name'],
            ))
            await conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "این نام مدل قبلاً وجود دارد")

        await log_activity("create_model", f"مدل {payload['name']} اضافه شد")
        return {"ok": True}
    finally:
        await conn.close()


@app.put("/api/models/{model_id}", dependencies=[Depends(require_auth)])
async def update_model(model_id: int, payload: dict):
    # ✅ اعتبارسنجی ورودی‌ها (اگه داده شدن)
    if 'name' in payload and not _is_valid_name(payload['name']):
        raise HTTPException(400, "نام باید فقط حروف کوچک، عدد، - و _ باشد")
    if 'base_url' in payload and not _is_valid_url(payload['base_url']):
        raise HTTPException(400, "Base URL نامعتبر")
    if 'api_key' in payload and len(payload['api_key']) < 10:
        raise HTTPException(400, "API Key خیلی کوتاه")

    allowed = ['name', 'display_name', 'api_key', 'base_url', 'model_name', 'is_active']
    fields, values = [], []
    for f in allowed:
        if f in payload:
            fields.append(f"{f} = ?")
            if f == 'api_key':
                values.append(encrypt(payload[f]))
            else:
                values.append(payload[f])
    if not fields:
        raise HTTPException(400, "فیلدی برای بروزرسانی نیست")

    fields.append("updated_at = ?")
    values.append(datetime.now().isoformat())
    values.append(model_id)

    conn = await get_db()
    try:
        await conn.execute(
            f"UPDATE ai_models SET {', '.join(fields)} WHERE id = ?", values
        )
        await conn.commit()
        await log_activity("update_model", f"مدل {model_id} بروزرسانی شد")
        return {"ok": True}
    finally:
        await conn.close()


@app.delete("/api/models/{model_id}", dependencies=[Depends(require_auth)])
async def delete_model(model_id: int):
    conn = await get_db()
    try:
        async with conn.execute(
            "SELECT is_default, name FROM ai_models WHERE id = ?", (model_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "مدل پیدا نشد")
        if row['is_default']:
            raise HTTPException(400, "مدل پیش‌فرض را نمی‌توان حذف کرد")

        await conn.execute("DELETE FROM ai_models WHERE id = ?", (model_id,))
        await conn.commit()
        await log_activity("delete_model", f"مدل {row['name']} حذف شد")
        return {"ok": True}
    finally:
        await conn.close()


@app.post("/api/models/{model_id}/set-default", dependencies=[Depends(require_auth)])
async def set_default_model(model_id: int):
    conn = await get_db()
    try:
        async with conn.execute(
            "SELECT name FROM ai_models WHERE id = ?", (model_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "مدل پیدا نشد")

        await conn.execute("UPDATE ai_models SET is_default = 0")
        await conn.execute(
            "UPDATE ai_models SET is_default = 1 WHERE id = ?", (model_id,)
        )
        await conn.commit()
        await log_activity("set_default_model", f"مدل {row['name']} پیش‌فرض شد")
        return {"ok": True}
    finally:
        await conn.close()


# ===========================================================================
# Settings
# ===========================================================================
@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings():
    conn = await get_db()
    try:
        async with conn.execute("SELECT key, value FROM bot_settings") as cur:
            rows = await cur.fetchall()
        return {row['key']: row['value'] for row in rows}
    finally:
        await conn.close()


@app.put("/api/settings", dependencies=[Depends(require_auth)])
async def update_settings(payload: dict):
    conn = await get_db()
    try:
        for key, value in payload.items():
            await conn.execute('''
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES (?, ?, ?)
            ''', (key, str(value), datetime.now().isoformat()))
        await conn.commit()
        await log_activity("update_settings", f"{len(payload)} تنظیم بروزرسانی شد")
        return {"ok": True}
    finally:
        await conn.close()


# ===========================================================================
# Users
# ===========================================================================
@app.get("/api/users", dependencies=[Depends(require_auth)])
async def list_users(limit: int = 100, offset: int = 0):
    conn = await get_db()
    try:
        async with conn.execute('''
            SELECT
                u.user_id AS id,
                u.username,
                u.first_name,
                u.is_admin,
                u.is_blocked AS is_banned,
                u.joined_date,
                u.last_active,
                COALESCE(COUNT(m.id), 0) AS messages_count
            FROM users u
            LEFT JOIN messages m ON m.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY messages_count DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# ===========================================================================
# Logs
# ===========================================================================
@app.get("/api/logs", dependencies=[Depends(require_auth)])
async def get_logs(limit: int = 50):
    conn = await get_db()
    try:
        async with conn.execute(
            "SELECT * FROM activity_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# ===========================================================================
# Frontend
# ===========================================================================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # بررسی auth
    token = request.cookies.get(SESSION_COOKIE)
    if not is_valid_session(token):
        return RedirectResponse("/login")

    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Not found</h1>"
# ---------------------------------------------------------------------------
_BC_DB = str(Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent)) / "bot.db")
broadcast_state = {"sent": 0, "failed": 0, "done": True, "total": 0}

BROADCAST_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>پیام همگانی</title>
<style>
body{font-family:Vazirmatn,Tahoma,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;justify-content:center;padding:24px}
.card{background:#1e293b;border-radius:16px;padding:28px;width:100%;max-width:640px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{font-size:20px;margin:0 0 16px}
textarea{width:100%;min-height:160px;background:#0b1220;color:#e2e8f0;border:1px solid #334155;border-radius:10px;padding:12px;font-family:inherit;font-size:14px;box-sizing:border-box}
button{margin-top:14px;width:100%;background:#2563eb;color:#fff;border:0;border-radius:10px;padding:12px;font-size:15px;cursor:pointer}
button:disabled{background:#475569}
#status{margin-top:14px;font-size:14px;color:#94a3b8}
a{color:#60a5fa}
</style>
</head>
<body>
<div class="card">
<h1>📣 ارسال پیام همگانی</h1>
<textarea id="text" placeholder="متن پیام خود را بنویسید..."></textarea>
<button id="send" onclick="send()">ارسال به همه کاربران</button>
<div id="status"></div>
<p><a href="/">← بازگشت به داشبورد</a></p>
</div>
<script>
let timer=null;
async function send(){
  const text=document.getElementById('text').value.trim();
  if(!text){alert('متن خالی است');return;}
  if(!confirm('پیام به همه کاربران ارسال شود؟'))return;
  document.getElementById('send').disabled=true;
  const fd=new FormData(); fd.append('text',text);
  const r=await fetch('/api/broadcast',{method:'POST',body:fd});
  const j=await r.json();
  if(j.total===0){document.getElementById('status').innerText='کاربری یافت نشد!';document.getElementById('send').disabled=false;return;}
  document.getElementById('status').innerText='در حال ارسال به '+j.total+' کاربر...';
  timer=setInterval(poll,2000);
}
async function poll(){
  const r=await fetch('/api/broadcast/status');
  const j=await r.json();
  document.getElementById('status').innerText='ارسال‌شده: '+j.sent+' | ناموفق: '+j.failed+' | از '+j.total;
  if(j.done){clearInterval(timer);document.getElementById('send').disabled=false;document.getElementById('status').innerText+=' ✅ تمام شد';}
}
</script>
</body>
</html>"""

def _bc_user_ids():
    conn = sqlite3.connect(_BC_DB)
    cur = conn.cursor()
    ids = []
    for col in ("user_id", "id", "chat_id"):
        try:
            cur.execute(f"SELECT {col} FROM users")
            ids = [r[0] for r in cur.fetchall()]
            break
        except sqlite3.OperationalError:
            continue
    conn.close()
    return ids

async def _bc_send(text: str):
    import httpx as _httpx
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ids = _bc_user_ids()
    sent = failed = 0
    async with _httpx.AsyncClient(timeout=15) as client:
        for uid in ids:
            try:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": uid, "text": text},
                )
                if r.status_code == 200:
                    sent += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            broadcast_state.update({"sent": sent, "failed": failed})
            await asyncio.sleep(0.05)
    broadcast_state.update({"sent": sent, "failed": failed, "done": True})

@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request):
    if not is_valid_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/login")
    return BROADCAST_HTML

@app.post("/api/broadcast")
async def api_broadcast(request: Request):
    if not is_valid_session(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401)
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="متن خالی است")
    ids = _bc_user_ids()
    broadcast_state.update({"sent": 0, "failed": 0, "done": False, "total": len(ids)})
    asyncio.create_task(_bc_send(text))
    return {"ok": True, "total": len(ids)}

@app.get("/api/broadcast/status")
async def api_broadcast_status(request: Request):
    if not is_valid_session(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401)
    return broadcast_state







@app.post("/api/models/{model_id}/toggle", dependencies=[Depends(require_auth)])
async def toggle_model(model_id: int):
    """تغییر وضعیت فعال/غیرفعال بودن مدل"""
    conn = await get_db()
    try:
        # بررسی وجود مدل
        cur = await conn.execute("SELECT is_active FROM ai_models WHERE id = ?", (model_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="مدل پیدا نشد")
        
        current_status = row['is_active']
        new_status = 0 if current_status else 1
        
        # آپدیت وضعیت
        await conn.execute(
            "UPDATE ai_models SET is_active = ? WHERE id = ?",
            (new_status, model_id)
        )
        await conn.commit()
        
        # ثبت لاگ
        status_text = 'غیرفعال' if new_status == 0 else 'فعال'
        await log_activity("toggle_model", f"مدل {model_id} {status_text} شد")
        
        return {
            "success": True,
            "is_active": bool(new_status),
            "message": f"مدل {status_text} شد"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
