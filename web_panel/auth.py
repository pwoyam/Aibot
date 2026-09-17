"""احراز هویت پنل ادمین — Basic Auth با session cookie + rate limiting"""
import os
import secrets
import hashlib
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request, HTTPException, status, Depends

# از .env خونده می‌شه
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
SESSION_SECRET = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
SESSION_COOKIE = "aibot_session"
SESSION_TTL_HOURS = 24

# Rate limiting
MAX_LOGIN_ATTEMPTS = 5          # حداکثر تلاش
LOGIN_WINDOW_SECONDS = 300      # در ۵ دقیقه
LOGIN_BLOCK_SECONDS = 900       # بلاک ۱۵ دقیقه‌ای بعد از عبور

# ذخیره session های فعال
_active_sessions: dict[str, datetime] = {}

# ذخیره تلاش‌های login (IP -> لیست timestamp ها)
_login_attempts: dict[str, list[float]] = defaultdict(list)
# IP های بلاک‌شده (IP -> زمان پایان بلاک)
_blocked_ips: dict[str, float] = {}


def _hash(text: str) -> str:
    return hashlib.sha256((SESSION_SECRET + text).encode()).hexdigest()


def create_session() -> str:
    """ساخت توکن session جدید"""
    token = secrets.token_urlsafe(32)
    _active_sessions[token] = datetime.now() + timedelta(hours=SESSION_TTL_HOURS)
    # پاکسازی session های منقضی
    now = datetime.now()
    expired = [t for t, exp in _active_sessions.items() if exp < now]
    for t in expired:
        _active_sessions.pop(t, None)
    return token


def is_valid_session(token: Optional[str]) -> bool:
    if not token:
        return False
    exp = _active_sessions.get(token)
    if not exp:
        return False
    if exp < datetime.now():
        _active_sessions.pop(token, None)
        return False
    return True


def verify_credentials(username: str, password: str) -> bool:
    """بررسی صحت یوزرنیم و پسورد"""
    if not PANEL_PASSWORD:
        return False
    return (
        secrets.compare_digest(username, PANEL_USERNAME)
        and secrets.compare_digest(password, PANEL_PASSWORD)
    )


def is_login_rate_limited(ip: str) -> tuple[bool, int]:
    """
    بررسی rate limit برای login.
    برمی‌گرداند: (is_limited, seconds_until_unblock)
    """
    now = time.monotonic()

    # بررسی بلاک فعال
    block_until = _blocked_ips.get(ip, 0)
    if block_until > now:
        return True, int(block_until - now)

    # پاک کردن تلاش‌های قدیمی
    attempts = _login_attempts[ip]
    attempts[:] = [t for t in attempts if now - t < LOGIN_WINDOW_SECONDS]

    # اگه از حد عبور کرده
    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        _blocked_ips[ip] = now + LOGIN_BLOCK_SECONDS
        attempts.clear()
        return True, LOGIN_BLOCK_SECONDS

    return False, 0


def record_login_attempt(ip: str, success: bool) -> None:
    """ثبت تلاش login"""
    if success:
        # موفق: پاک کردن تلاش‌ها و بلاک
        _login_attempts.pop(ip, None)
        _blocked_ips.pop(ip, None)
    else:
        _login_attempts[ip].append(time.monotonic())


async def require_auth(request: Request):
    """Dependency برای محافظت از endpointها"""
    token = request.cookies.get(SESSION_COOKIE)
    if not is_valid_session(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="لطفاً وارد شوید",
        )
    return True


def get_client_ip(request: Request) -> str:
    """IP کاربر رو با احتساب پروکسی بگیر"""
    # Railway پروکسی داره، پس X-Forwarded-For مهمه
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
