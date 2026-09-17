from dotenv import load_dotenv
load_dotenv()
"""مدیریت تنظیمات پویا از دیتابیس (نسخه async با aiosqlite)"""
import aiosqlite
from pathlib import Path
from typing import Dict, Any, List, Optional

import os
DATA_DIR = Path(os.getenv('DATA_DIR', Path(__file__).resolve().parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = str(DATA_DIR / 'bot.db')


async def _connect():
    conn = await aiosqlite.connect(DATABASE_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


class ConfigManager:
    """مدیریت تنظیمات ربات از دیتابیس"""

    @staticmethod
    async def get_setting(key: str, default: Any = None) -> Any:
        try:
            conn = await _connect()
            try:
                async with conn.execute(
                    "SELECT value FROM bot_settings WHERE key = ?", (key,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row['value'] if row else default
            finally:
                await conn.close()
        except Exception:
            return default

    @staticmethod
    async def get_bool_setting(key: str, default: bool = True) -> bool:
        value = await ConfigManager.get_setting(key)
        if value is None:
            return default
        return str(value) == '1'

    @staticmethod
    async def get_int_setting(key: str, default: int = 0) -> int:
        value = await ConfigManager.get_setting(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    async def get_all_settings() -> Dict[str, Any]:
        try:
            conn = await _connect()
            try:
                async with conn.execute("SELECT key, value FROM bot_settings") as cursor:
                    rows = await cursor.fetchall()
                    return {row['key']: row['value'] for row in rows}
            finally:
                await conn.close()
        except Exception:
            return {}

    @staticmethod
    async def is_feature_enabled(feature: str) -> bool:
        return await ConfigManager.get_bool_setting(f'enable_{feature}', True)


class ModelManager:
    """مدیریت مدل‌های AI از دیتابیس"""

    @staticmethod
    async def get_all_models() -> List[Dict[str, Any]]:
        try:
            conn = await _connect()
            try:
                async with conn.execute('''
                    SELECT id, name, display_name, api_key, base_url,
                           model_name, is_default, is_active
                    FROM ai_models WHERE is_active = 1
                    ORDER BY is_default DESC, name
                ''') as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
            finally:
                await conn.close()
        except Exception:
            return []

    @staticmethod
    async def get_default_model() -> Optional[Dict[str, Any]]:
        try:
            conn = await _connect()
            try:
                async with conn.execute('''
                    SELECT id, name, display_name, api_key, base_url, model_name
                    FROM ai_models WHERE is_default = 1 AND is_active = 1
                    LIMIT 1
                ''') as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                await conn.close()
        except Exception:
            return None

    @staticmethod
    async def get_model_by_name(name: str) -> Optional[Dict[str, Any]]:
        try:
            conn = await _connect()
            try:
                async with conn.execute('''
                    SELECT id, name, display_name, api_key, base_url, model_name
                    FROM ai_models WHERE name = ? AND is_active = 1
                ''', (name,)) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                await conn.close()
        except Exception:
            return None


# --- توابع کمکی async ---

async def get_daily_message_limit() -> int:
    return await ConfigManager.get_int_setting('daily_message_limit', 10)

async def is_chat_enabled() -> bool:
    return await ConfigManager.is_feature_enabled('chat')

async def is_translate_enabled() -> bool:
    return await ConfigManager.is_feature_enabled('translate')

async def is_summarize_enabled() -> bool:
    return await ConfigManager.is_feature_enabled('summarize')

async def is_url_summarize_enabled() -> bool:
    return await ConfigManager.is_feature_enabled('url_summarize')

async def is_pdf_download_enabled() -> bool:
    return await ConfigManager.is_feature_enabled('pdf_download')

async def get_welcome_message() -> str:
    return await ConfigManager.get_setting(
        'welcome_message', 'سلام! به ربات هوش مصنوعی خوش آمدید 🤖'
    )

async def get_bot_name() -> str:
    return await ConfigManager.get_setting('bot_name', 'ربات هوش مصنوعی')
