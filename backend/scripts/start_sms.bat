@echo off
REM ============================================================================
REM  start_sms.bat - 1 lan bam: build + chay API (Docker) roi khoi dong lai Worker
REM  Cach dung: double-click file nay (KHONG can Run as administrator)
REM    1. Mo Docker Desktop neu chua chay, cho Docker san sang
REM    2. docker compose up -d --build  (API + seed DB)
REM    3. Tat Worker dang chay (neu co) roi mo lai Worker o quyen THUONG
REM  Luu y: tat Worker khi job dang chay -> lan chay do bi danh dau loi (chay lai la duoc)
REM ============================================================================
setlocal
cd /d %~dp0..
title SMS - khoi dong

echo [1/3] Kiem tra Docker...
docker info >nul 2>&1
if not errorlevel 1 goto dockerok
echo     Docker chua chay -^> mo Docker Desktop
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a n=0
:waitdocker
ping -n 6 127.0.0.1 >nul
docker info >nul 2>&1
if not errorlevel 1 goto dockerok
set /a n+=1
if %n% lss 36 goto waitdocker
echo     LOI: Docker khong san sang sau 3 phut. Mo Docker Desktop bang tay roi chay lai file nay.
pause
exit /b 1
:dockerok
echo     Docker OK

echo [2/3] docker compose up -d --build ...
docker compose up -d --build
if errorlevel 1 (
  echo     LOI: docker compose that bai - xem thong bao o tren.
  pause
  exit /b 1
)

echo [3/3] Khoi dong lai Worker...
taskkill /FI "WINDOWTITLE eq SMS Worker*" /T /F >nul 2>&1
REM Tat ca vong lap cmd (run_worker.bat) lan python run_worker - neu chi tat python thi vong lap mo lai sau 15s
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*run_worker.bat*') -or ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*run_worker*') } | Sort-Object { $_.Name -ne 'cmd.exe' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
ping -n 3 127.0.0.1 >nul

schtasks /Query /TN "SMS Worker" >nul 2>&1
if not errorlevel 1 (
  REM Task Scheduler chay Worker bang quyen THUONG (dung voi WPS/MES)
  schtasks /End /TN "SMS Worker" >nul 2>&1
  schtasks /Run /TN "SMS Worker" >nul
  echo     Da chay lai task "SMS Worker"
  goto done
)
net session >nul 2>&1
if not errorlevel 1 (
  REM Dang o quyen Admin -> mo Worker o quyen thuong (Worker chay Admin se khong thay WPS / Excel.Application)
  runas /trustlevel:0x20000 "cmd /c start \"SMS Worker\" \"%~dp0run_worker.bat\""
) else (
  start "SMS Worker" "%~dp0run_worker.bat"
)
echo     Da mo cua so SMS Worker

:done
echo.
echo Xong. API: http://localhost:8000/api/health  -  Worker: cua so "SMS Worker"
ping -n 6 127.0.0.1 >nul
endlocal
