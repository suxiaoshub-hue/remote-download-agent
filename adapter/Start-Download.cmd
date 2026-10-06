@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 请先打开 PCStory，并确认已配置好游戏的下载磁盘。
echo 此测试会实际尝试新增下载任务。
echo.
PcstoryAdapter.exe
echo.
echo 退出码：%errorlevel%
echo 结果文件：%~dp0pcstory-download.txt
pause
