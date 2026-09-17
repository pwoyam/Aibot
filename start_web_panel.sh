#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "🚀 راه‌اندازی پنل مدیریت..."

# فعال کردن venv
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ venv پیدا نشد. اول venv بساز: python3 -m venv venv"
    exit 1
fi

# چک کردن .env
if [ ! -f ".env" ]; then
    echo "❌ فایل .env وجود ندارد"
    exit 1
fi

# چک PANEL_PASSWORD
if ! grep -q "^PANEL_PASSWORD=.\+" .env; then
    echo "❌ PANEL_PASSWORD در .env تنظیم نشده"
    echo "   یه رمز قوی توی .env بذار"
    exit 1
fi

# متوقف کردن قبلی
./stop_web_panel.sh 2>/dev/null || true
sleep 1

# اجرا
echo "⚙️  شروع FastAPI..."
nohup python web_panel/app.py > web_panel.log 2>&1 &
echo $! > web_panel.pid
echo "   ✅ PID: $(cat web_panel.pid)"

sleep 3

if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/login | grep -q "200"; then
    echo "   ✅ پنل آماده است: http://localhost:8000"
    echo ""
    echo "برای دسترسی از بیرون، از SSH tunnel استفاده کن:"
    echo "   ssh -L 8000:localhost:8000 user@your-server"
    echo ""
else
    echo "   ❌ خطا. لاگ:"
    tail -20 web_panel.log
    exit 1
fi
