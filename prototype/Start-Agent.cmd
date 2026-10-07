@echo off
cd /d "%~dp0"
chcp 65001 >nul
Agent.exe
echo.
pause
