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

打开 `http://127.0.0.1:8765/`，注册网吧后即可提交任务。Agent 默认使用模拟下载器；Windows 接入层可通过 `--pcstory-command` 调用：

```bat
python agent.py --cafe-id cafe-001 --name "测试网吧" --server http://127.0.0.1:8765 --pcstory-command "PcstoryAdapter.exe --game-id {game_id} --force {force}"
```
