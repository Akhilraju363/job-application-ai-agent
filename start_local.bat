@echo off
rem Double-click launcher for start_local.ps1 -- all startup logic lives in that script.
rem %~dp0 is this file's own folder, so it works from Explorer or any current directory.
rem Extra arguments are passed through, e.g.  start_local.bat -NoPipeline -Port 8766
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

rem Opened by double-click: keep the window so the last messages/errors stay readable.
echo %cmdcmdline% | "%SystemRoot%\System32\find.exe" /i "%~nx0" >nul && (
    echo.
    echo start_local.ps1 exited with code %EXITCODE%.
    pause
)
exit /b %EXITCODE%
