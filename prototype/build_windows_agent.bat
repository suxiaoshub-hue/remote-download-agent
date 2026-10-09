@echo off
setlocal
cd /d "%~dp0"
python -m pip install pyinstaller==5.13.2 -r requirements-server.txt
python -m PyInstaller --onefile --name Agent run_agent_config.py
python -m PyInstaller --onefile --name Server --add-data "web.html;." server.py
echo.
echo EXE 已生成：dist\Agent.exe
pause
