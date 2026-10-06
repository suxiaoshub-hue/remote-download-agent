# 网吧远程下载

服务端包含中文网页、网吧管理、游戏查询、任务队列及 SQLite 持久化。每家网吧使用独立 Agent 凭证主动连接服务器，无需开放网吧入站端口。Agent 自动只读 PCStory 数据库并上报其配置下载盘的容量，调用已验证的 PcstoryAdapter.exe 下发 GID 下载命令。

## Windows 单机测试

从 GitHub Actions 下载 remote-download-agent-windows.zip 并全部解压。测试机无需安装 Python。

1. 双击 Start-Server.cmd，保持窗口运行。服务端首次生成 server-config.json。
2. 浏览器打开 http://127.0.0.1:8765，使用 server-config.json 中的 adminToken 登录。
3. 点击“添加网吧”，服务器地址填写 http://127.0.0.1:8765，网页自动下载 agent-config.json。
4. 将该配置放在 Agent.exe 同目录，打开 PCStory。以和 PCStory 相同的管理员权限双击 Start-Agent.cmd。
5. 等待约 30 秒，网页选择网吧并搜索游戏，检查状态与下载盘容量。对未下载的游戏点击“下载”，确认 PCStory 实际新增并开始任务。
6. PCStory 标记游戏为本地已下载且目录存在后，下一次清单上报会更新云端任务。

无需手工导出 games.json。清单无法读取时网页显示具体原因，Agent 保留旧结果并禁止下发。可选 pcstoryFolder 是包含 pcstory.exe、pcstory.dat、config.ini 的目录；留空自动寻找正在运行的 PCStory。

本地日志为 agent.log、server.log、pcstory-task-任务编号.txt。不要使用旧版 Agent 配置，也不要把同一配置同时用于两台网吧机器。连接密钥只在添加网吧时提供，妥善保管配置。

## 云服务器部署

目前尚未部署公网服务。租用 Linux 云服务器后，给域名设置 DNS，开放 80/443，安装 Docker Compose：

```sh
git clone https://github.com/suxiaoshub-hue/remote-download-agent.git
cd remote-download-agent/deploy
cp .env.example .env
```

修改 .env 中的 DOWNLOAD_DOMAIN 为后台域名，运行：

```sh
docker compose up -d --build
docker compose exec server cat /data/server-config.json
```

Caddy 自动申请 HTTPS 证书；浏览器打开 https://你的域名，使用 adminToken 登录。添加网吧时填写相同 HTTPS 地址。各网吧只部署 Agent.exe、PcstoryAdapter.exe、pcstory-adapter.ini、Start-Agent.cmd 和网页生成的 agent-config.json。完整服务端源码无需第三方 Python 库，可自行通过 systemd 与 HTTPS 反向代理运行。

备份 Docker server-data 卷中的 server-config.json 和 remote_download.db。启动命令以同一个持久化数据目录运行；不要将凭证或数据库加入 Git。

## 状态与限制

“已下载”表示 PCStory 的本地记录为状态 1 且目录存在，不进行所有游戏文件的完整校验。状态 2/3/5/6 表示排队或暂停，不能据此宣称正在下载。原生命令的新日志确认开始后显示“已启动下载”，完成由后续库存确认；没有模拟进度百分比。

仅适配已经验证的 PCStory SHA256 `05b9927164f7b3a842f48ffc3bcfd464ae4052e2cdeec4f54902925f2178cdb6`。不同版本停止库存和命令操作。GID 8049 在此版本具有特殊强制更新逻辑，适配器拒绝执行。

命令结果不确定时禁止自动重发，避免重复下载；须在测试机查看 PCStory 和任务日志。仅取消尚未下发任务，暂停实际下载在 PCStory 中操作。磁盘检查不预留其他并发任务的空间，PCStory 最终处理容量和它自己的保留空间设置。

当前是单服务进程版本；库存或连接超过有效期停止下发。已完成本地测试和 GitHub 构建验证后仍需测试机验证自动读取与网页下载整个流程。Python 3.8 构建兼容旧系统，但旧 Windows 的完整运行兼容性以实际测试为准。

## 开发验证

```sh
python -m pip install cryptography==42.0.8
python -m unittest discover -s prototype -p 'test*.py'
```

cryptography 只用于独立密码算法测试，不属于 Agent 或服务端运行依赖。Actions 还验证冻结 EXE 运行、原生 IPC、退出码与 Windows 7 静态导入。
