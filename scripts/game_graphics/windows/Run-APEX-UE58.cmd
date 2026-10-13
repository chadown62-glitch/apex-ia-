@echo off
setlocal DisableDelayedExpansion
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-APEX-UE58.ps1" %*
set "APEX_UE_EXIT=%ERRORLEVEL%"
echo.
echo APEX exit code: %APEX_UE_EXIT% (0 = requested mode completed, 2 = blocked or failed).
pause
exit /b %APEX_UE_EXIT%
