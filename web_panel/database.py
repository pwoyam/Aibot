from dotenv import load_dotenv
load_dotenv()
"""دیتابیس پنل وب - async با aiosqlite و WAL mode"""
import os
from pathlib import Path

import aiosqlite
from crypto import encrypt

# مسیر دیتابیس نسبت به روت پروژه
import os as _os2
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(_os2.getenv('DATA_DIR', BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = str(DATA_DIR / 'bot.db')


async def get_db():
    """اتصال async به دیتابیس"""
    conn = await aiosqlite.connect(DATABASE_PATH)
    conn.row_factory = aiosqlite.Row
    # WAL mode برای همزمانی بهتر
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init_web_panel_tables():
    """ایجاد جداول پنل + migration جدول users"""
    conn = await get_db()
    try:
        # جدول مدل‌های AI
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS ai_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                api_key TEXT NOT NULL,
                base_url TEXT NOT NULL,
                model_name TEXT NOT NULL,
                is_default BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # جدول تنظیمات
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # جدول لاگ
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # تنظیمات پیش‌فرض
        defaults = {
            'daily_message_limit': '10',
            'welcome_message': 'سلام! به ربات هوش مصنوعی خوش آمدید 🤖',
            'bot_name': 'ربات هوش مصنوعی',
            'enable_chat': '1',
            'enable_translate': '1',
            'enable_summarize': '1',
            'enable_url_summarize': '1',
            'enable_pdf_download': '1',
        }
        for key, value in defaults.items():
            await conn.execute(
                'INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)',
                (key, value),
            )

        # مدل‌های پیش‌فرض فقط اگه جدول خالیه
        async with conn.execute("SELECT COUNT(*) FROM ai_models") as cur:
            row = await cur.fetchone()
            if row[0] == 0:
                # از env می‌خونیم
                import os as _os
                ds_key = _os.getenv("DEEPSEEK_API_KEY", "")
                ds_base = _os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
                ds_model = _os.getenv("MODEL_NAME", "deepseek-chat")
                if ds_key:
                    ds_key = encrypt(ds_key)
                    await conn.execute('''
                        INSERT INTO ai_models
                            (name, display_name, api_key, base_url, model_name, is_default)
                        VALUES (?, ?, ?, ?, ?, 1)
                    ''', ('deepseek', 'DeepSeek', ds_key, ds_base, ds_model))

                mm_key = _os.getenv("SECOND_API_KEY", "")
                if mm_key:
                    mm_key = encrypt(mm_key)
                    mm_base = _os.getenv("SECOND_BASE_URL", "https://api.routeway.ai/v1")
                    mm_model = _os.getenv("SECOND_MODEL_NAME", "muse-glimmer-30b:free")
                    await conn.execute('''
                        INSERT INTO ai_models
                            (name, display_name, api_key, base_url, model_name, is_default)
                        VALUES (?, ?, ?, ?, ?, 0)
                    ''', ('muse', 'Muse Glimmer 30B', mm_key, mm_base, mm_model))

        await conn.commit()
        print("✅ جداول پنل وب آماده شدند")
    finally:
        await conn.close()


async def log_activity(action: str, details: str = "", user_id: int = None):
    """ثبت لاگ فعالیت"""
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO activity_logs (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details),
        )
        await conn.commit()
    finally:
        await conn.close()
