"""
ربات هوش مصنوعی تلگرام (DeepSeek + چند مدل)
--------------------------------------------
ویژگی‌ها:
- چت استریم با AI
- ترجمه هوشمند
- خلاصه‌سازی متن
- خلاصه‌سازی لینک
- دانلود PDF
- پنل وب پویا
- پیام همگانی
- آمار و گزارش‌گیری
- دیتابیس async با aiosqlite
"""

import asyncio
import logging
import os
import re
import time
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
from urllib.parse import urlparse

import aiosqlite
import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from pathlib import Path as _P
import sys as _sys
_sys.path.insert(0, str(_P(__file__).resolve().parent))
from web_panel.crypto import decrypt

from config_manager import (
    ConfigManager, ModelManager,
    get_daily_message_limit, get_welcome_message, get_bot_name,
    is_chat_enabled, is_translate_enabled, is_summarize_enabled,
    is_url_summarize_enabled, is_pdf_download_enabled,
)


# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
SECOND_API_KEY = os.getenv("SECOND_API_KEY", "")
SECOND_BASE_URL = os.getenv("SECOND_BASE_URL", "https://api.routeway.ai/v1")
SECOND_MODEL_NAME = os.getenv("SECOND_MODEL_NAME", "muse-glimmer-30b:free")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "تو یک دستیار هوشمند و مفید هستی که به زبان کاربر (معمولاً فارسی) پاسخ می‌دهی. "
    "پاسخ‌ها را کوتاه، دقیق و کاربردی نگه دار مگر کاربر جزئیات بیشتری بخواهد.",
)

MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
STREAM_EDIT_INTERVAL = float(os.getenv("STREAM_EDIT_INTERVAL", "0.7"))
MIN_SECONDS_BETWEEN_REQUESTS = float(os.getenv("MIN_SECONDS_BETWEEN_REQUESTS", "1.0"))

from pathlib import Path as _Path
DATA_DIR = _Path(os.getenv('DATA_DIR', _Path(__file__).resolve().parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = str(DATA_DIR / 'bot.db')

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_ai_bot")


# ---------------------------------------------------------------------------
# حافظه‌ی مکالمه
# ---------------------------------------------------------------------------
conversations: dict = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
user_modes: dict = {}
user_model_preferences: dict = {}
user_last_responses: dict = {}
admin_broadcast_cache: dict = {}
last_request_time: dict = {}

ADMIN_USER_IDS = [
    int(id.strip())
    for id in os.getenv("ADMIN_USER_IDS", "").split(",")
    if id.strip()
]
if not ADMIN_USER_IDS:
    logger.warning("⚠️ ADMIN_USER_IDS خالی است. هیچ‌کس ادمین نیست.")

deepseek_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
minimax_client = (
    AsyncOpenAI(api_key=SECOND_API_KEY, base_url=SECOND_BASE_URL, timeout=60.0)
    if SECOND_API_KEY else None
)


# ---------------------------------------------------------------------------
# دیتابیس (async)
# ---------------------------------------------------------------------------
async def init_db():
    """ساخت جداول دیتابیس"""
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id INTEGER, date TEXT, count INTEGER,
                PRIMARY KEY (user_id, date)
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT,
                first_name TEXT, is_admin INTEGER DEFAULT 0,
                joined_date TEXT, last_active TEXT,
                is_blocked INTEGER DEFAULT 0
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, message_type TEXT, timestamp TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT, sent_date TEXT,
                recipient_count INTEGER, admin_id INTEGER
            )
        ''')
        await conn.commit()
    logger.info("✅ دیتابیس راه‌اندازی شد")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


async def get_daily_count(user_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        async with conn.execute(
            "SELECT count FROM daily_usage WHERE user_id=? AND date=?",
            (user_id, date.today().isoformat())
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def increment_daily_count(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute("""
            INSERT INTO daily_usage (user_id, date, count) VALUES (?, ?, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1
        """, (user_id, date.today().isoformat()))
        await conn.commit()


async def check_daily_limit(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    daily_limit = await get_daily_message_limit()
    count = await get_daily_count(user_id)
    return count < daily_limit


async def get_remaining_messages(user_id: int) -> str:
    if is_admin(user_id):
        return "♾️ نامحدود (ادمین)"
    daily_limit = await get_daily_message_limit()
    count = await get_daily_count(user_id)
    remaining = max(0, daily_limit - count)
    return f"{remaining} از {daily_limit}"


async def register_user(user_id: int, username: str = "", first_name: str = "", is_admin_flag: bool = False):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        now = datetime.now().isoformat()
        await conn.execute('''
            INSERT INTO users (user_id, username, first_name, is_admin, joined_date, last_active)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                is_admin = excluded.is_admin,
                last_active = excluded.last_active
        ''', (user_id, username, first_name, 1 if is_admin_flag else 0, now, now))
        await conn.commit()


async def log_message(user_id: int, message_type: str = "chat"):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute(
            "INSERT INTO messages (user_id, message_type, timestamp) VALUES (?, ?, ?)",
            (user_id, message_type, datetime.now().isoformat())
        )
        await conn.commit()


async def get_total_stats():
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        async with conn.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM daily_usage WHERE date=?", (today,)
        ) as cur:
            active_today = (await cur.fetchone())[0]
        async with conn.execute("SELECT COUNT(*) FROM messages") as cur:
            total_messages = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(timestamp)=?", (today,)
        ) as cur:
            messages_today = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT message_type, COUNT(*) FROM messages GROUP BY message_type"
        ) as cur:
            type_counts = dict(await cur.fetchall())

    return {
        "total_users": total_users,
        "active_today": active_today,
        "total_messages": total_messages,
        "messages_today": messages_today,
        "chat_count": type_counts.get("chat", 0),
        "translate_count": type_counts.get("translate", 0),
        "summarize_count": type_counts.get("summarize", 0),
        "url_summarize_count": type_counts.get("url_summarize", 0),
    }


async def get_top_users(limit=10):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        async with conn.execute('''
            SELECT u.user_id, u.username, u.first_name, COUNT(m.id) as msg_count
            FROM users u
            LEFT JOIN messages m ON u.user_id = m.user_id
            WHERE u.is_blocked = 0
            GROUP BY u.user_id
            ORDER BY msg_count DESC
            LIMIT ?
        ''', (limit,)) as cur:
            return await cur.fetchall()


async def get_all_user_ids():
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        async with conn.execute(
            "SELECT user_id FROM users WHERE is_blocked = 0"
        ) as cur:
            rows = await cur.fetchall()
            return [row[0] for row in rows]


async def get_active_user_ids(days=7):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        async with conn.execute(
            "SELECT DISTINCT user_id FROM daily_usage WHERE date >= ?", (cutoff,)
        ) as cur:
            rows = await cur.fetchall()
            return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health server running on port {port}")
    server.serve_forever()


# ---------------------------------------------------------------------------
# توابع کمکی
# ---------------------------------------------------------------------------
def is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    last = last_request_time.get(user_id, 0.0)
    if now - last < MIN_SECONDS_BETWEEN_REQUESTS:
        return True
    last_request_time[user_id] = now
    return False


def filter_think_tags(text: str) -> str:
    """حذف تگ‌های thinking/reasoning از پاسخ مدل"""
    if not text:
        return ""
    # حذف تگ‌های کامل
    text = re.sub(r'<think[^>]*>.*?</think[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking[^>]*>.*?</thinking[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<reasoning[^>]*>.*?</reasoning[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thought[^>]*>.*?</thought[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # حذف تگ‌های باز که هنوز بسته نشدن (در حالت streaming)
    text = re.sub(r'<think[^>]*>.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking[^>]*>.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<reasoning[^>]*>.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    # حذف تگ‌های بسته‌ی بدون باز
    text = re.sub(r'.*?</think[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'.*?</thinking[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'.*?</reasoning[^>]*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ('http', 'https'), result.netloc])
    except Exception:
        return False


async def get_ai_client(user_id: int):
    """کلاینت و مدل مناسب برای کاربر را برمی‌گرداند (پویا از دیتابیس)"""
    try:
        user_pref = user_model_preferences.get(user_id)
        if user_pref:
            selected = await ModelManager.get_model_by_name(user_pref)
            if selected and selected.get('is_active', 1):
                client = AsyncOpenAI(
                    api_key=decrypt(selected['api_key']),
                    base_url=selected['base_url'],
                    timeout=60.0,
                )
                return client, selected['model_name'], selected['display_name']
            else:
                logger.info(f"مدل '{user_pref}' برای کاربر {user_id} دیگه در دسترس نیست")
                user_model_preferences.pop(user_id, None)

        default_model = await ModelManager.get_default_model()
        if default_model:
            client = AsyncOpenAI(
                api_key=decrypt(default_model['api_key']),
                base_url=default_model['base_url'],
                timeout=60.0,
            )
            return client, default_model['model_name'], default_model['display_name']
    except Exception as e:
        logger.warning(f"خطا در دریافت مدل از دیتابیس: {e}")

    return deepseek_client, MODEL_NAME, "DeepSeek"


# ---------------------------------------------------------------------------
# استریم AI
# ---------------------------------------------------------------------------
async def stream_ai_reply(user_id: int, user_text: str, on_update):
    history = conversations[user_id]
    history.append({"role": "user", "content": user_text})

    client, model_name, display_name = await get_ai_client(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    full_text = ""
    last_edit_time = 0.0
    start_time = time.monotonic()

    try:
        stream = await client.chat.completions.create(
            model=model_name,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=messages,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            full_text += delta
            now = time.monotonic()
            if now - last_edit_time >= STREAM_EDIT_INTERVAL:
                last_edit_time = now
                await on_update(filter_think_tags(full_text))

        user_last_responses[user_id] = full_text
        await on_update(filter_think_tags(full_text), final=True)
        history.append({"role": "assistant", "content": full_text})
        
        latency = time.monotonic() - start_time
        await update_model_latency(model_name, latency, success=True)
        
        return full_text
    except Exception as e:
        latency = time.monotonic() - start_time
        await update_model_latency(model_name, latency, success=False)
        raise


async def translate_text(user_id: int, text: str, on_update):
    client, model_name, display_name = await get_ai_client(user_id)
    messages = [
        {
            "role": "system",
            "content": (
                "تو یک مترجم حرفه‌ای و دقیق هستی.\n"
                "قوانین:\n"
                "1. اگر متن فارسی است، آن را به انگلیسی روان ترجمه کن.\n"
                "2. اگر متن انگلیسی یا زبان دیگری است، به فارسی روان ترجمه کن.\n"
                "3. فقط ترجمه را برگردان، بدون هیچ توضیح اضافه.\n"
                "4. لحن و معنای اصلی را حفظ کن.\n"
            )
        },
        {"role": "user", "content": text}
    ]

    full_text = ""
    last_edit_time = 0.0
    stream = await client.chat.completions.create(
        model=model_name, max_tokens=MAX_OUTPUT_TOKENS,
        messages=messages, stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if not delta:
            continue
        full_text += delta
        now = time.monotonic()
        if now - last_edit_time >= STREAM_EDIT_INTERVAL:
            last_edit_time = now
            await on_update(filter_think_tags(full_text))

    remaining = await get_remaining_messages(user_id)
    footer = f"\n\n---\n🤖 {display_name} | 🌍 ترجمه | 📊 باقی‌مانده: {remaining}"
    user_last_responses[user_id] = full_text
    await on_update(filter_think_tags(full_text) + footer, final=True)
    return full_text


async def summarize_text(user_id: int, text: str, on_update):
    client, model_name, display_name = await get_ai_client(user_id)
    messages = [
        {
            "role": "system",
            "content": (
                "تو یک متخصص خلاصه‌سازی متن هستی.\n"
                "متن را خلاصه، مفید و روان بازنویسی کن.\n"
                "نکات کلیدی را حفظ کن. فقط خلاصه را برگردان."
            )
        },
        {"role": "user", "content": text}
    ]

    full_text = ""
    last_edit_time = 0.0
    stream = await client.chat.completions.create(
        model=model_name, max_tokens=MAX_OUTPUT_TOKENS,
        messages=messages, stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if not delta:
            continue
        full_text += delta
        now = time.monotonic()
        if now - last_edit_time >= STREAM_EDIT_INTERVAL:
            last_edit_time = now
            await on_update(filter_think_tags(full_text))

    remaining = await get_remaining_messages(user_id)
    footer = f"\n\n---\n🤖 {display_name} | 📝 خلاصه‌سازی | 📊 باقی‌مانده: {remaining}"
    user_last_responses[user_id] = full_text
    await on_update(filter_think_tags(full_text) + footer, final=True)
    return full_text


async def extract_text_from_url(url: str, max_chars: int = 5000) -> str:
    if not is_valid_url(url):
        raise ValueError("URL نامعتبر است. لطفاً یک لینک معتبر با http یا https بفرستید.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }

    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text


async def summarize_url_content(user_id: int, url: str, text: str, on_update):
    client, model_name, display_name = await get_ai_client(user_id)
    messages = [
        {
            "role": "system",
            "content": (
                "تو یک متخصص خلاصه‌سازی محتوا هستی.\n"
                "محتوای صفحه وب را حداکثر ۳۰۰ کلمه خلاصه کن.\n"
                "نکات کلیدی را حفظ کن. خلاصه را به فارسی بنویس.\n"
                "فقط خلاصه را برگردان."
            )
        },
        {"role": "user", "content": f"URL: {url}\n\nمحتوا:\n{text}"}
    ]

    full_text = ""
    last_edit_time = 0.0
    stream = await client.chat.completions.create(
        model=model_name, max_tokens=MAX_OUTPUT_TOKENS,
        messages=messages, stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if not delta:
            continue
        full_text += delta
        now = time.monotonic()
        if now - last_edit_time >= 0.5:
            last_edit_time = now
            await on_update(filter_think_tags(full_text))

    remaining = await get_remaining_messages(user_id)
    footer = f"\n\n---\n🤖 {display_name} | 🔗 خلاصه لینک | 📊 باقی‌مانده: {remaining}"
    user_last_responses[user_id] = full_text
    await on_update(filter_think_tags(full_text) + footer, final=True)
    return full_text


# ---------------------------------------------------------------------------
# پیام همگانی
# ---------------------------------------------------------------------------
async def send_broadcast(bot, admin_id: int, content: str, recipient_ids: list):
    sent_count = 0
    failed_count = 0
    failed_users = []
    total = len(recipient_ids)

    for i, user_id in enumerate(recipient_ids, 1):
        try:
            await bot.send_message(chat_id=user_id, text=content)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed_count += 1
            failed_users.append(user_id)
            logger.warning(f"ارسال به {user_id} ناموفق: {e}")

        if i % 30 == 0:
            logger.info(f"پیشرفت ارسال: {i}/{total}")

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute('''
            INSERT INTO broadcasts (content, sent_date, recipient_count, admin_id)
            VALUES (?, ?, ?, ?)
        ''', (content[:500], datetime.now().isoformat(), sent_count, admin_id))
        await conn.commit()

    return {
        "total": total,
        "sent": sent_count,
        "failed": failed_count,
        "failed_users": failed_users[:10]
    }


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def generate_pdf_from_text(text: str, user_id: int) -> str:
    from fpdf import FPDF
    import arabic_reshaper
    from bidi.algorithm import get_display

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    persian_font_path = 'fonts/NotoSansArabic.ttf'
    english_font_path = 'fonts/NotoSans.ttf'

    if not os.path.exists(persian_font_path):
        raise FileNotFoundError(f"فونت فارسی یافت نشد: {persian_font_path}")
    if not os.path.exists(english_font_path):
        raise FileNotFoundError(f"فونت انگلیسی یافت نشد: {english_font_path}")

    pdf.add_font('PersianFont', '', persian_font_path)
    pdf.add_font('EnglishFont', '', english_font_path)

    def is_mostly_persian(text_str):
        persian_chars = len(re.findall(r'[\u0600-\u06FF]', text_str))
        latin_chars = len(re.findall(r'[a-zA-Z]', text_str))
        total = persian_chars + latin_chars
        if total == 0:
            return False
        return persian_chars > latin_chars

    for para in text.split('\n'):
        para = para.strip()
        if not para:
            pdf.ln(3)
            continue
        try:
            if is_mostly_persian(para):
                pdf.set_font('PersianFont', size=11)
                reshaped = arabic_reshaper.reshape(para)
                bidi = get_display(reshaped)
                pdf.set_x(10)
                pdf.multi_cell(0, 7, bidi, align='R')
            else:
                pdf.set_font('EnglishFont', size=11)
                pdf.set_x(10)
                pdf.multi_cell(0, 7, para, align='L')
            pdf.ln(2)
        except Exception:
            pdf.set_font('EnglishFont', size=11)
            pdf.set_x(10)
            pdf.multi_cell(0, 7, para, align='L')
            pdf.ln(2)

    file_path = f'/tmp/ai_response_{user_id}.pdf'
    pdf.output(file_path)
    return file_path


# ---------------------------------------------------------------------------
# منوی اصلی
# ---------------------------------------------------------------------------
async def get_main_menu(user_id=None):
    """کیبورد منوی اصلی — با نام مدل پویا از دیتابیس"""
    model_display = "DeepSeek"
    try:
        if user_id:
            user_pref = user_model_preferences.get(user_id)
            if user_pref:
                model = await ModelManager.get_model_by_name(user_pref)
                if model and model.get('is_active', 1):
                    model_display = model['display_name']
                else:
                    user_model_preferences.pop(user_id, None)

        if model_display == "DeepSeek":
            default_model = await ModelManager.get_default_model()
            if default_model:
                model_display = default_model['display_name']
    except Exception as e:
        logger.warning(f"خطا در ساخت منو: {e}")

    keyboard = [
        [InlineKeyboardButton("💬 شروع چت", callback_data="chat")],
        [
            InlineKeyboardButton("🌍 ترجمه", callback_data="translate"),
            InlineKeyboardButton("📝 خلاصه‌سازی", callback_data="summarize"),
        ],
        [InlineKeyboardButton("🔗 خلاصه لینک", callback_data="url_summarize")],
        [InlineKeyboardButton(f"🤖 مدل: {model_display}", callback_data="switch_model")],
        [InlineKeyboardButton("🔄 ریست حافظه", callback_data="reset")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# هندلرها
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations.pop(user_id, None)
    reply_markup = await get_main_menu(user_id)
    await update.message.reply_text(
        "سلام! من یک ربات هوش مصنوعی هستم 🤖\nاز منوی زیر انتخاب کن:",
        reply_markup=reply_markup,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()

    if query.data == "chat":
        user_modes.pop(user_id, None)
        await query.edit_message_text(
            "💬 **حالت چت فعال شد!**\n\nهر سوالی داری بپرس.\n\nبرای بازگشت به منو: /start",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif query.data == "send_all":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ فقط ادمین.")
            return
        content = admin_broadcast_cache.pop(user_id, None)
        user_modes.pop(user_id, None)
        if not content:
            await query.edit_message_text("❌ پیامی برای ارسال وجود ندارد.")
            return
        await query.edit_message_text("📢 در حال ارسال به همه کاربران...")
        all_users = await get_all_user_ids()
        result = await send_broadcast(context.bot, user_id, content, all_users)
        text = (
            "✅ **ارسال کامل شد!**\n\n"
            f"📊 کل: {result['total']}\n"
            f"✅ موفق: {result['sent']}\n"
            f"❌ ناموفق: {result['failed']}\n\n"
            f"🏠 /start"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

    elif query.data == "send_active":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ فقط ادمین.")
            return
        content = admin_broadcast_cache.pop(user_id, None)
        user_modes.pop(user_id, None)
        if not content:
            await query.edit_message_text("❌ پیامی برای ارسال وجود ندارد.")
            return
        await query.edit_message_text("🔥 در حال ارسال به کاربران فعال...")
        active_users = await get_active_user_ids(7)
        result = await send_broadcast(context.bot, user_id, content, active_users)
        text = (
            "✅ **ارسال به فعال‌ها کامل شد!**\n\n"
            f"📊 کل: {result['total']}\n"
            f"✅ موفق: {result['sent']}\n"
            f"❌ ناموفق: {result['failed']}\n\n"
            f"🏠 /start"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

    elif query.data == "cancel_broadcast":
        user_modes.pop(user_id, None)
        admin_broadcast_cache.pop(user_id, None)
        await query.edit_message_text("❌ لغو شد.\n\n🏠 /start")

    elif query.data == "switch_model":
        # ✅ پویا از دیتابیس با آمار سرعت
        models = await get_model_stats()

        if not models:
            await query.edit_message_text(
                "❌ هیچ مدلی تعریف نشده.\n"
                "از پنل ادمین یه مدل اضافه کن.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")]
                ])
            )
            return

        current = user_model_preferences.get(user_id)
        keyboard = []
        for m in models:
            mark = " ✅" if m['name'] == current else ""
            cb = f"select_model_{m['name']}"[:64]
            emoji = get_status_emoji(m.get('latency_avg', 0), m.get('success_count', 0), m.get('request_count', 0))
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {m['display_name']}{mark}",
                    callback_data=cb
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_menu")])

        await query.edit_message_text(
            "🤖 **انتخاب مدل هوش مصنوعی**\n\n"
            "یکی از مدل‌های زیر را انتخاب کنید:\n\n"
            "💡 علامت ✅ مدل فعال فعلی را نشان می‌دهد.\n"
            "🟢 سریع | 🟡 متوسط | 🔴 شلوغ | ⚪ بدون داده",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data.startswith("select_model_"):
        # ✅ داینامیک — هر مدلی که توی دیتابیس باشه
        model_name = query.data[len("select_model_"):]

        model = await ModelManager.get_model_by_name(model_name)
        if not model:
            await query.answer("❌ این مدل دیگه وجود نداره!", show_alert=True)
            await query.edit_message_text(
                "❌ این مدل حذف یا غیرفعال شده.\n"
                "یه مدل دیگه انتخاب کن.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="switch_model")]
                ])
            )
            return

        user_model_preferences[user_id] = model_name
        await query.edit_message_text(
            f"✅ **{model['display_name']} فعال شد!**\n\n"
            f"این مدل برای چت، ترجمه و خلاصه‌سازی استفاده می‌شود.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")]
            ]),
        )

    elif query.data == "translate":
        user_modes[user_id] = "translate"
        await query.edit_message_text(
            "🌍 **حالت ترجمه**\n\nمتن را بفرست.\nفارسی→انگلیسی یا انگلیسی→فارسی",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")]
            ]),
        )

    elif query.data == "summarize":
        user_modes[user_id] = "summarize"
        await query.edit_message_text(
            "📝 **حالت خلاصه‌سازی**\n\nمتن طولانی را بفرست.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")]
            ]),
        )

    elif query.data == "url_summarize":
        user_modes[user_id] = "url_summarize"
        await query.edit_message_text(
            "🔗 **خلاصه لینک**\n\nلینک را بفرست.\nمثال: https://example.com",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")]
            ]),
        )

    elif query.data == "reset":
        conversations.pop(user_id, None)
        await query.edit_message_text("✅ حافظه پاک شد!\n\n/start")

    elif query.data == "download_pdf":
        if not await is_pdf_download_enabled():
            await query.answer("⚠️ این قابلیت غیرفعال شده", show_alert=True)
            return
        if user_id not in user_last_responses:
            await query.answer("❌ پاسخی برای دانلود وجود ندارد", show_alert=True)
            return

        await query.answer("⏳ در حال ساخت PDF...")

        text = user_last_responses[user_id]
        try:
            file_path = await asyncio.to_thread(generate_pdf_from_text, text, user_id)

            with open(file_path, 'rb') as pdf_file:
                await query.message.reply_document(
                    document=pdf_file,
                    filename="AI_Response.pdf",
                    caption="📄 پاسخ شما به فرمت PDF",
                )

            import os as _os
            if _os.path.exists(file_path):
                _os.remove(file_path)

        except Exception as e:
            logger.exception("خطا در تولید PDF")
            await query.message.reply_text("❌ خطا در ساخت PDF. دوباره تلاش کن.")

    elif query.data == "back_to_menu":
        user_modes.pop(user_id, None)
        reply_markup = await get_main_menu(user_id)
        await query.edit_message_text(
            "🏠 **به منوی اصلی برگشتی!**\n\nاز منوی زیر انتخاب کن:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif query.data == "help":

        await query.edit_message_text(
            "🤖 **راهنما**\n\n"
            "💬 چت هوشمند\n"
            "🌍 ترجمه\n"
            "📝 خلاصه‌سازی\n"
            "🔗 خلاصه لینک\n\n"
            "دستورات ادمین:\n"
            "/stats /users /topusers /broadcast",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")]
            ]),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (
        "🤖 **راهنمای ربات**\n\n"
        "💬 چت هوشمند\n🌍 ترجمه\n📝 خلاصه‌سازی\n🔗 خلاصه لینک\n\n"
        "برای بازگشت به منو: /start"
    )
    if is_admin(user_id):
        text += (
            "\n\n👑 **دستورات ادمین:**\n"
            "/stats /users /topusers /broadcast"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations.pop(update.effective_user.id, None)
    await update.message.reply_text("حافظه‌ی مکالمه پاک شد. از نو شروع کن!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    if not user_text:
        return

    if is_rate_limited(user_id):
        await update.message.reply_text("یکم آروم‌تر! چند ثانیه صبر کن.")
        return

    user = update.effective_user
    await register_user(
        user_id=user_id,
        username=user.username or "",
        first_name=user.first_name or "",
        is_admin_flag=is_admin(user_id),
    )

    # حالت broadcast
    if user_modes.get(user_id) == "broadcast":
        if not is_admin(user_id):
            user_modes.pop(user_id, None)
            await update.message.reply_text("⛔ فقط ادمین.")
            return
        admin_broadcast_cache[user_id] = user_text
        all_users = await get_all_user_ids()
        active_users = await get_active_user_ids(7)
        keyboard = [
            [InlineKeyboardButton(f"📢 ارسال به همه ({len(all_users)})", callback_data="send_all")],
            [InlineKeyboardButton(f"🔥 ارسال به فعال‌ها ({len(active_users)})", callback_data="send_active")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_broadcast")],
        ]
        preview = (
            "📋 **پیش‌نمایش:**\n"
            "━━━━━━━━━━━━━━━\n"
            f"{user_text}\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"👥 همه: {len(all_users)} | 🔥 فعال (۷ روز): {len(active_users)}\n\n"
            "🎯 انتخاب کنید:"
        )
        await update.message.reply_text(
            preview, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # حالت خلاصه‌سازی
    if user_modes.get(user_id) == "summarize":
        if not await is_summarize_enabled():
            await update.message.reply_text("⚠️ قابلیت خلاصه‌سازی توسط ادمین غیرفعال شده.")
            return
        if not await check_daily_limit(user_id):
            limit = await get_daily_message_limit()
            await update.message.reply_text(
                f"⚠️ به سقف {limit} پیام روزانه رسیدی. فردا دوباره تلاش کن."
            )
            return
        await increment_daily_count(user_id)
        await log_message(user_id, "summarize")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        sent_message = await update.message.reply_text("📝 در حال خلاصه‌سازی...")

        async def on_update(text: str, final: bool = False):
            display_text = text if text.strip() else "..."
            if len(display_text) > 4000:
                display_text = display_text[-4000:]
            reply_markup = None
            if final:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📄 دانلود PDF", callback_data="download_pdf")],
                    [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")],
                ])
            try:
                if final:
                    await sent_message.edit_text(display_text, reply_markup=reply_markup)
                else:
                    await sent_message.edit_text(display_text)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logger.warning("خطا در ویرایش: %s", e)

        try:
            await summarize_text(user_id, user_text, on_update)
        except Exception:
            logger.exception("خطا در خلاصه‌سازی")
            try:
                await sent_message.edit_text("❌ خطا در خلاصه‌سازی.")
            except Exception:
                pass
        return

    # حالت خلاصه لینک
    if user_modes.get(user_id) == "url_summarize":
        if not await is_url_summarize_enabled():
            await update.message.reply_text("⚠️ قابلیت خلاصه لینک توسط ادمین غیرفعال شده.")
            return
        if not is_valid_url(user_text):
            await update.message.reply_text(
                "⚠️ لینک معتبر بفرست (http:// یا https://)."
            )
            return
        if not await check_daily_limit(user_id):
            limit = await get_daily_message_limit()
            await update.message.reply_text(
                f"⚠️ به سقف {limit} پیام روزانه رسیدی."
            )
            return
        await increment_daily_count(user_id)
        await log_message(user_id, "url_summarize")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        processing_msg = await update.message.reply_text("🔗 در حال دانلود...")

        try:
            page_text = await extract_text_from_url(user_text)
            if not page_text or len(page_text) < 100:
                await processing_msg.edit_text("❌ متن کافی استخراج نشد.")
                return
            await processing_msg.edit_text("📝 در حال خلاصه‌سازی...")

            async def on_update(text: str, final: bool = False):
                display_text = text if text.strip() else "..."
                if len(display_text) > 4000:
                    display_text = display_text[:4000] + "\n\n...(برش خورد)"
                reply_markup = None
                if final:
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📄 دانلود PDF", callback_data="download_pdf")],
                        [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")],
                    ])
                try:
                    if final:
                        await processing_msg.edit_text(display_text, reply_markup=reply_markup)
                    else:
                        await processing_msg.edit_text(display_text)
                except Exception as e:
                    if "not modified" not in str(e).lower():
                        logger.warning("خطا در ویرایش: %s", e)

            await summarize_url_content(user_id, user_text, page_text, on_update)
        except Exception:
            logger.exception("خطا در خلاصه لینک")
            try:
                await processing_msg.edit_text("❌ خطا در خلاصه‌سازی لینک.")
            except Exception:
                pass
        return

    # حالت ترجمه
    if user_modes.get(user_id) == "translate":
        if not await is_translate_enabled():
            await update.message.reply_text("⚠️ قابلیت ترجمه توسط ادمین غیرفعال شده.")
            return
        if not await check_daily_limit(user_id):
            limit = await get_daily_message_limit()
            await update.message.reply_text(
                f"⚠️ به سقف {limit} پیام روزانه رسیدی."
            )
            return
        await increment_daily_count(user_id)
        await log_message(user_id, "translate")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        sent_message = await update.message.reply_text("🌍 در حال ترجمه...")

        async def on_update(text: str, final: bool = False):
            display_text = text if text.strip() else "..."
            if len(display_text) > 4000:
                display_text = display_text[-4000:]
            reply_markup = None
            if final:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📄 دانلود PDF", callback_data="download_pdf")],
                    [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")],
                ])
            try:
                if final:
                    await sent_message.edit_text(display_text, reply_markup=reply_markup)
                else:
                    await sent_message.edit_text(display_text)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logger.warning("خطا در ویرایش: %s", e)

        try:
            await translate_text(user_id, user_text, on_update)
        except Exception:
            logger.exception("خطا در ترجمه")
            try:
                await sent_message.edit_text("❌ خطا در ترجمه.")
            except Exception:
                pass
        return

    # حالت چت (پیش‌فرض)
    if not await is_chat_enabled():
        await update.message.reply_text("⚠️ قابلیت چت توسط ادمین غیرفعال شده.")
        return
    if not await check_daily_limit(user_id):
        limit = await get_daily_message_limit()
        await update.message.reply_text(
            f"⚠️ به سقف {limit} پیام روزانه رسیدی. فردا دوباره تلاش کن."
        )
        return

    await increment_daily_count(user_id)
    await log_message(user_id, "chat")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    sent_message = await update.message.reply_text("در حال فکر کردن... ⏳")

    async def on_update(text: str, final: bool = False):
        display_text = text if text.strip() else "..."
        if len(display_text) > 4000:
            display_text = display_text[-4000:]
        reply_markup = None
        if final:
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 دانلود PDF", callback_data="download_pdf")],
                [InlineKeyboardButton("🏠 بازگشت", callback_data="back_to_menu")],
            ])
        try:
            if final:
                await sent_message.edit_text(display_text, reply_markup=reply_markup)
            else:
                await sent_message.edit_text(display_text)
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning("خطا در ویرایش: %s", e)

    try:
        await stream_ai_reply(user_id, user_text, on_update)
    except Exception:
        logger.exception("خطا در دریافت پاسخ AI")
        try:
            await sent_message.edit_text("❌ خطا در دریافت پاسخ. دوباره تلاش کن.")
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطای غیرمنتظره: %s", context.error)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return
    stats = await get_total_stats()
    total = stats["chat_count"] + stats["translate_count"] + stats["summarize_count"]
    chat_pct = round(stats["chat_count"] / total * 100) if total else 0
    tr_pct = round(stats["translate_count"] / total * 100) if total else 0
    sm_pct = round(stats["summarize_count"] / total * 100) if total else 0
    text = (
        "📊 **آمار ربات**\n\n"
        f"👥 کل کاربران: {stats['total_users']}\n"
        f"🟢 فعال امروز: {stats['active_today']}\n\n"
        f"💬 کل پیام‌ها: {stats['total_messages']}\n"
        f"📅 امروز: {stats['messages_today']}\n\n"
        f"🤖 چت: {stats['chat_count']} ({chat_pct}٪)\n"
        f"🌍 ترجمه: {stats['translate_count']} ({tr_pct}٪)\n"
        f"📝 خلاصه: {stats['summarize_count']} ({sm_pct}٪)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return
    top_users = await get_top_users(20)
    if not top_users:
        await update.message.reply_text("هنوز کاربری ثبت نشده.")
        return
    text = "👥 **کاربران (۲۰ نفر اول)**\n\n"
    for i, (uid, username, first_name, msg_count) in enumerate(top_users, 1):
        name = first_name or username or f"کاربر {uid}"
        text += f"{i}. 👤 {name} ({uid}) - {msg_count} پیام\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def topusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return
    top_users = await get_top_users(10)
    if not top_users:
        await update.message.reply_text("هنوز کاربری ثبت نشده.")
        return
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    text = "🏆 **۱۰ کاربر پرمصرف**\n\n"
    for i, (uid, username, first_name, msg_count) in enumerate(top_users, 1):
        name = first_name or username or f"کاربر {uid}"
        medal = medals[i-1] if i <= len(medals) else f"{i}."
        text += f"{medal} {name} ({uid}) - {msg_count} پیام\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return
    user_modes[user_id] = "broadcast"
    await update.message.reply_text(
        "📢 **حالت پیام همگانی**\n\nپیام خود را بفرستید.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_broadcast")]
        ]),
    )


# ---------------------------------------------------------------------------
# اجرا
# ---------------------------------------------------------------------------
async def post_init(application: Application):
    await init_db()


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("❌ TELEGRAM_BOT_TOKEN تنظیم نشده.")
    if not DEEPSEEK_API_KEY:
        raise SystemExit("❌ DEEPSEEK_API_KEY تنظیم نشده.")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("topusers", topusers_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    threading.Thread(target=run_health_server, daemon=True).start()

    logger.info("🚀 ربات در حال اجراست...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)



# ---------------------------------------------------------------------------
# آمار و سرعت مدل‌ها
# ---------------------------------------------------------------------------
async def get_model_stats():
    """دریافت آمار همه مدل‌ها برای نمایش در منو"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute('''
                SELECT name, display_name, latency_avg, request_count, success_count, is_default, is_active
                FROM ai_models WHERE is_active = 1
                ORDER BY is_default DESC, name
            ''') as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"خطا در get_model_stats: {e}")
        return []

async def update_model_latency(model_name: str, latency: float, success: bool):
    """بروزرسانی آمار تأخیر و موفقیت یک مدل"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT latency_avg, request_count, success_count FROM ai_models WHERE name = ?",
                (model_name,)
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return
                old_avg = row['latency_avg'] or 0.0
                old_count = row['request_count'] or 0
                old_success = row['success_count'] or 0
            
            new_count = old_count + 1
            new_success = old_success + (1 if success else 0)
            if old_count == 0:
                new_avg = latency
            else:
                new_avg = (old_avg * old_count + latency) / new_count
            
            await conn.execute('''
                UPDATE ai_models 
                SET latency_avg = ?, request_count = ?, success_count = ?
                WHERE name = ?
            ''', (new_avg, new_count, new_success, model_name))
            await conn.commit()
    except Exception as e:
        logger.warning(f"خطا در بروزرسانی آمار: {e}")

def get_status_emoji(latency_avg: float, success_count: int, request_count: int) -> str:
    """تولید ایموجی وضعیت بر اساس آمار"""
    if request_count < 3:
        return "⚪"
    success_rate = (success_count / request_count) * 100 if request_count > 0 else 0
    if success_rate < 80 or latency_avg > 4.0:
        return "🔴"
    elif success_rate < 95 or latency_avg > 2.0:
        return "🟡"
    else:
        return "🟢"

if __name__ == "__main__":
    main()
