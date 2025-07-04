#!/bin/bash

# الحصول على قيمة PORT من متغير البيئة مع قيمة افتراضية 8080
PORT=${PORT:-8080}

# تنفيذ Gunicorn مع البورت الصحيح
exec gunicorn --bind 0.0.0.0:$PORT --timeout 600 --workers 1 --worker-class sync --access-logfile - --error-logfile - server:app