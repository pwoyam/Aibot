#!/bin/bash

echo "🛑 توقف پنل مدیریت..."

# متوقف کردن tunnel
if [ -f tunnel.pid ]; then
    kill $(cat tunnel.pid) 2>/dev/null
    rm tunnel.pid
    echo "   ✅ Tunnel متوقف شد"
fi

# متوقف کردن سرور
if [ -f web_panel.pid ]; then
    kill $(cat web_panel.pid) 2>/dev/null
    rm web_panel.pid
    echo "   ✅ سرور FastAPI متوقف شد"
fi

# بررسی فرآیندهای باقی‌مانده
pkill -f "cloudflared tunnel" 2>/dev/null
pkill -f "web_panel/app.py" 2>/dev/null

echo "✅ همه سرویس‌ها متوقف شدند"
