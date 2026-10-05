@echo off
setlocal
python -m pip install --upgrade pyinstaller
python -m PyInstaller --onefile --name Agent agent.py
echo.
echo EXE 已生成：dist\Agent.exe
pause
