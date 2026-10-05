@echo off
setlocal
set AGENT_DIR=%~dp0
copy "%AGENT_DIR%agent-config.json.example" "%AGENT_DIR%agent-config.json"
echo 已生成 agent-config.json，请填写 cafeId、name 和 server。
echo 可将以下命令放入 Windows 启动目录：
echo python "%AGENT_DIR%run_agent_config.py" --config "%AGENT_DIR%agent-config.json"
pause
