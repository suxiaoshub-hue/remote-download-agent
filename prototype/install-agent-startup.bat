@echo off
setlocal
set AGENT_DIR=%~dp0
echo 请先从网页下载 agent-config.json，放到 Agent.exe 同目录。
echo 可将以下命令放入 Windows 启动目录：
echo "%AGENT_DIR%Agent.exe"
pause
