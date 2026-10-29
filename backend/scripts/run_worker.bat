@echo off
REM Chạy worker thực thi job — phải chạy trong phiên Windows đang đăng nhập
cd /d %~dp0..
call .venv\Scripts\activate.bat
python manage.py run_worker
