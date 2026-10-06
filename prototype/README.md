# 网吧远程下载原型

本原型包含一个中央服务和一个网吧 Agent，使用 Python 标准库运行。

启动中央服务：

```bash
python3 server.py
```

启动 Agent：

```bash
python3 agent.py --cafe-id cafe-001 --name "测试网吧" --server http://127.0.0.1:8765
```

Agent 可以定时上报本地清单 JSON 和下载磁盘容量。清单可使用之前
`PcstoryReader` 导出的 `games.json`；Agent 会检查 `LocalPath` 是否存在，
再把状态标为 `installed` 或 `missing`。示例：

```bash
python3 agent.py --cafe-id cafe-001 --name "测试网吧" \
  --server http://127.0.0.1:8765 --inventory-file games.json --disk-path D:\
```

网页选择网吧后搜索游戏，会显示已下载、文件缺失、未确认和磁盘剩余空间。
当前原型仍用模拟任务进度；真实下载命令由 `PcstoryAdapter.exe` 执行，
接入时应根据其退出码和 PCStory 日志更新任务状态。

打开 `http://127.0.0.1:8765/`，注册网吧后即可提交任务。Agent 默认使用模拟下载器；Windows 接入层可通过 `--pcstory-command` 调用：

```bat
python agent.py --cafe-id cafe-001 --name "测试网吧" --server http://127.0.0.1:8765 --pcstory-command "PcstoryAdapter.exe --game-id {game_id} --force {force}"
```
