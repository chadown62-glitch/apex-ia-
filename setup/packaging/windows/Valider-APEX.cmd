@echo off
rem Validation APEX Desktop sur ce PC : double-cliquer. Aucun droit administrateur requis.
rem Le script trouve seul les installateurs de ce dossier et pose quelques questions O/N.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Test-ApexWindows.ps1" -Interactive
pause
