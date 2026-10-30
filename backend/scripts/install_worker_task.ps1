# Cài worker SMS tự chạy khi đăng nhập Windows (Task Scheduler).
# Chạy 1 lần trong PowerShell (Run as Administrator):
#   powershell -ExecutionPolicy Bypass -File C:\SCAN_MES\SMS\backend\scripts\install_worker_task.ps1
# Task chạy bằng tài khoản đang đăng nhập, quyền THƯỜNG (WPS đăng ký COM theo người dùng — chạy quyền Admin sẽ không thấy WPS),
# không giới hạn thời gian chạy, tự khởi động lại khi bị dừng.
$TaskName = "SMS Worker"
$User = "$env:USERDOMAIN\$env:USERNAME"
$Bat = Join-Path $PSScriptRoot "run_worker.bat"

$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Bat`"" -WorkingDirectory (Split-Path $PSScriptRoot)
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
               -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Da cai '$TaskName' cho $User va chay ngay. Kiem tra: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
