@echo off
REM Chạy worker thực thi job — phải chạy trong phiên Windows đang đăng nhập, quyền THƯỜNG (không Run as admin)
REM Worker dừng/lỗi → tự chạy lại sau 15 giây. Nhật ký: backend\logs\worker_YYYYMMDD.log
chcp 65001 >nul
title SMS Worker - KHONG TAT CUA SO NAY (thu nho duoc)
cd /d %~dp0..
if not exist logs mkdir logs
call .venv\Scripts\activate.bat
:loop
python manage.py run_worker
echo [%date% %time%] Worker dung (ma loi %errorlevel%), chay lai sau 15 giay>> logs\worker_restart.log
REM timeout lỗi (Windows đang đăng xuất, không có console) → dùng ping để không lặp chạy lại hàng trăm lần/giây
timeout /t 15 /nobreak >nul 2>&1 || ping -n 16 127.0.0.1 >nul
goto loop
