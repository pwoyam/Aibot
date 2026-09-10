"""
ربات هوش مصنوعی تلگرام (DeepSeek)
------------------------
یک ربات تلگرامی که پیام کاربر را می‌گیرد، به مدل هوش مصنوعی DeepSeek
می‌فرستد و پاسخ را به صورت استریم (پله‌پله) و بهینه برمی‌گرداند.

DeepSeek یک API سازگار با فرمت OpenAI ارائه می‌دهد، پس از کتابخانه‌ی
رسمی `openai` با یک base_url متفاوت استفاده می‌کنیم.

ویژگی‌های بهینه‌سازی:
- استفاده کامل از async/await برای هندل کردن هم‌زمان چند کاربر بدون بلاک شدن.
- استریم کردن پاسخ مدل: پیام تلگرام هر چند صدم ثانیه ویرایش می‌شود تا کاربر
  خیلی زود شروع پاسخ را ببیند (به جای منتظر ماندن برای کل پاسخ).
- محدود کردن طول حافظه‌ی مکالمه هر کاربر (فقط N پیام آخر) تا درخواست‌ها
  سبک و سریع بمانند و هزینه/تاخیر مدل پایین بیاید.
- Connection pooling از طریق کلاینت AsyncOpenAI (httpx زیرساختی).
- محدودیت نرخ درخواست ساده به ازای هر کاربر برای جلوگیری از اسپم/سوءاستفاده.
- مدیریت خطا (timeout، محدودیت نرخ API، خطای شبکه) با پیام مناسب به کاربر.
"""

import asyncio
import logging
import os
import time
from collections import defaultdict, deque

from openai import AsyncOpenAI
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# مدل‌های موجود در DeepSeek: "deepseek-chat" (V3، برای مکالمه‌ی عمومی و سریع)
# یا "deepseek-reasoner" (R1، برای استدلال عمیق‌تر ولی کندتر)
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "تو یک دستیار هوشمند و مفید هستی که به زبان کاربر (معمولاً فارسی) پاسخ می‌دهی."
    " پاسخ‌ها را کوتاه، دقیق و کاربردی نگه دار مگر کاربر جزئیات بیشتری بخواهد.",
)

MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))  # تعداد پیام‌های اخیر نگه‌داری‌شده
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
STREAM_EDIT_INTERVAL = float(os.getenv("STREAM_EDIT_INTERVAL", "0.7"))  # ثانیه بین ویرایش‌های پیام
MIN_SECONDS_BETWEEN_REQUESTS = float(os.getenv("MIN_SECONDS_BETWEEN_REQUESTS", "1.0"))  # ریت‌لیمیت ساده هر کاربر

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_ai_bot")

# ---------------------------------------------------------------------------
# حافظه‌ی مکالمه (در حافظه‌ی برنامه - برای مقیاس بزرگ‌تر می‌توان Redis گذاشت)
# ---------------------------------------------------------------------------

# هر کاربر: deque از دیکشنری‌های {"role": ..., "content": ...}
conversations: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
last_request_time: dict[int, float] = {}

deepseek_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


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


async def stream_ai_reply(user_id: int, user_text: str, on_update):
    """
    درخواست استریم به DeepSeek (سازگار با فرمت OpenAI) می‌فرستد و به ازای هر
    تکه‌ی متن جدید، on_update(full_text_so_far) را صدا می‌زند.
    در پایان، متن کامل پاسخ را برمی‌گرداند.
    """
    history = conversations[user_id]
    history.append({"role": "user", "content": user_text})

    # DeepSeek از فرمت OpenAI پیروی می‌کند: پیام سیستم هم داخل لیست messages می‌آید
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    full_text = ""
    last_edit_time = 0.0

    stream = await deepseek_client.chat.completions.create(
        model=MODEL_NAME,
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
            await on_update(full_text)

    # آخرین به‌روزرسانی با متن کامل و نهایی
    await on_update(full_text, final=True)

    history.append({"role": "assistant", "content": full_text})
    return full_text


# ---------------------------------------------------------------------------
# هندلرهای تلگرام
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "سلام! من یک ربات هوش مصنوعی هستم 🤖\n"
        "هر سوالی داری بپرس تا برات جواب بدم.\n\n"
        "دستورات:\n"
        "/reset — پاک کردن حافظه‌ی مکالمه\n"
        "/help — راهنما"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "فقط پیامت رو بفرست، من با هوش مصنوعی جوابت رو می‌دم.\n"
        "با /reset می‌تونی تاریخچه‌ی مکالمه رو پاک کنی تا از اول شروع کنیم."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations.pop(update.effective_user.id, None)
    await update.message.reply_text("حافظه‌ی مکالمه پاک شد. از نو شروع کن!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if not user_text:
        return

    if is_rate_limited(user_id):
        await update.message.reply_text("یکم آروم‌تر! لطفاً چند ثانیه صبر کن و دوباره بفرست.")
        return

    # نشان دادن وضعیت "در حال تایپ" تا کاربر بداند ربات مشغول است
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # یک پیام خالی/اولیه ارسال می‌کنیم و بعد آن را با پاسخ استریم‌شده ویرایش می‌کنیم
    sent_message = await update.message.reply_text("در حال فکر کردن... ⏳")

    async def on_update(text: str, final: bool = False):
        display_text = text if text.strip() else "..."
        # تلگرام محدودیت طول پیام (۴۰۹۶ کاراکتر) دارد
        if len(display_text) > 4000:
            display_text = display_text[-4000:]
        try:
            await sent_message.edit_text(display_text)
        except Exception as e:  # نادیده گرفتن خطاهای "message not modified" و مشابه
            if "not modified" not in str(e).lower():
                logger.warning("خطا در ویرایش پیام: %s", e)

    try:
        await stream_ai_reply(user_id, user_text, on_update)
    except Exception as e:
        logger.exception("خطا هنگام دریافت پاسخ از هوش مصنوعی")
        try:
            await sent_message.edit_text(
                "متاسفم، مشکلی پیش اومد و نتونستم جواب بدم. لطفاً دوباره امتحان کن."
            )
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطای غیرمنتظره: %s", context.error)


# ---------------------------------------------------------------------------
# اجرای برنامه
# ---------------------------------------------------------------------------

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("لطفاً متغیر محیطی TELEGRAM_BOT_TOKEN را تنظیم کنید.")
    if not DEEPSEEK_API_KEY:
        raise SystemExit("لطفاً متغیر محیطی DEEPSEEK_API_KEY را تنظیم کنید.")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("ربات در حال اجراست...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
