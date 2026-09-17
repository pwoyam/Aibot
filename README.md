# 🤖 ربات هوش مصنوعی تلگرام + پنل ادمین

ربات تلگرامی چند-مدلی با پنل مدیریت وب برای اضافه کردن مدل‌های AI.

## ✨ ویژگی‌ها

### ربات تلگرام
- 💬 چت هوشمند با استریم پاسخ
- 🌍 ترجمه هوشمند (فارسی ↔ انگلیسی)
- 📝 خلاصه‌سازی متن
- 🔗 خلاصه‌سازی لینک
- 📄 دانلود PDF از پاسخ‌ها
- 🤖 چند-مدلی
- 👑 دستورات ادمین
- 📊 آمار کاربران
- 🔒 محدودیت روزانه

### پنل ادمین (FastAPI)
- 🔐 احراز هویت با session + rate limiting
- 🧠 مدیریت مدل‌ها (CRUD)
- ⚙️ تنظیمات پویا
- 👥 مدیریت کاربران
- 📋 لاگ فعالیت‌ها
- 🔒 رمزنگاری API Key با Fernet

## 🏗️ معماری

ربات و پنل دیتابیس مشترک (bot.db) دارن — مدلی که توی پنل اضافه میشه، بلافاصله توی ربات دیده میشه.

## 🚀 نصب

### لوکال

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    python bot.py

### Docker

    docker build -t aibot .
    docker run -d --name aibot --env-file .env -v $(pwd)/data:/app/data -p 8080:8080 aibot

### Railway

1. ریپو رو وصل کن
2. Volume با mount path /app/data بساز
3. Variables رو پر کن
4. Deploy

## 🔧 متغیرهای محیطی

| متغیر | توضیح |
|-------|-------|
| TELEGRAM_BOT_TOKEN | توکن ربات |
| DEEPSEEK_API_KEY | کلید DeepSeek |
| ADMIN_USER_IDS | آیدی عددی ادمین‌ها |
| ENCRYPTION_KEY | کلید رمزنگاری |
| PANEL_USERNAME | یوزرنیم پنل |
| PANEL_PASSWORD | پسورد پنل |
| SECRET_KEY | سکرت سشن |
| DATA_DIR | مسیر دیتابیس |
| PORT | پورت health check |

## 🔒 امنیت

- Rate limiting روی login
- Session cookie امن
- رمزنگاری API Key
- CORS محدود
- اعتبارسنجی ورودی

⚠️ ENCRYPTION_KEY رو گم نکن!

## 📁 ساختار

    Aibot/
    ├── bot.py
    ├── config_manager.py
    ├── requirements.txt
    ├── Dockerfile
    ├── .env.example
    ├── web_panel/
    │   ├── app.py
    │   ├── auth.py
    │   ├── crypto.py
    │   ├── database.py
    │   ├── models.py
    │   ├── templates/index.html
    │   └── static/
    └── fonts/

## 📄 License

MIT
