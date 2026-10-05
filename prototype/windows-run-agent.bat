@echo off
cd /d "%~dp0"
python agent.py --cafe-id cafe-001 --name "测试网吧" --server "http://127.0.0.1:8765" --pcstory-command "PcstoryAdapter.exe --game-id {game_id} --force {force}"
pause
