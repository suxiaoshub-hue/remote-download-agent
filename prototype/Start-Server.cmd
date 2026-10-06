@echo off
cd /d "%~dp0"
echo Web: http://127.0.0.1:8765
echo Login key: adminToken in server-config.json
echo Startup errors: server.log
Server.exe > server.log 2>&1
echo.
echo Check server.log for server startup errors.
pause
