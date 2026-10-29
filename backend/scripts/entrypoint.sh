#!/bin/sh
# Khởi động container API: cập nhật bảng + dữ liệu mặc định rồi chạy gunicorn
set -e
python manage.py migrate --noinput
python manage.py seed
# --timeout 120: API tích hợp có thể giữ kết nối chờ job tối đa 90 giây
# access log dùng %(U)s = đường dẫn KHÔNG kèm query → không ghi lộ ?password=... vào log
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" --threads "${GUNICORN_THREADS:-4}" \
  --timeout 120 --error-logfile - --access-logfile - \
  --access-logformat '%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)ss'
