# 网吧远程下载

服务端包含中文网页、网吧管理、游戏查询、任务队列及 SQLite 持久化。每家网吧使用独立 Agent 凭证主动连接服务器，无需开放网吧入站端口。Agent 自动只读 PCStory 数据库并上报其配置下载盘的容量，调用已验证的 PcstoryAdapter.exe 下发 GID 下载命令。

## Windows 单机测试

从 GitHub Actions 下载 remote-download-agent-windows.zip 并全部解压。测试机无需安装 Python。

1. 双击 Start-Server.cmd，保持窗口运行。服务端首次生成 server-config.json。
2. 浏览器打开 http://127.0.0.1:8765，使用 server-config.json 中的 adminToken 登录。
3. 点击“添加网吧”，服务器地址填写 http://127.0.0.1:8765，网页自动下载 agent-config.json。
4. 将该配置放在 Agent.exe 同目录，打开 PCStory。以和 PCStory 相同的管理员权限双击 Start-Agent.cmd。
5. 等待约 30 秒，在首页点击对应网吧进入二级页，输入游戏名称或 GID 搜索，查看状态与下载盘容量。对未下载的游戏点击“下发下载”，确认 PCStory 实际新增并开始任务。未输入搜索词时不展示完整游戏清单。
6. PCStory 标记游戏为本地已下载且目录存在后，下一次清单上报会更新云端任务。

无需手工导出 games.json。清单无法读取时网页显示具体原因，Agent 保留旧结果并禁止下发。可选 pcstoryFolder 是包含 pcstory.exe、pcstory.dat、config.ini 的目录；留空自动寻找正在运行的 PCStory。

本地日志为 agent.log、server.log、pcstory-task-任务编号.txt。日志和重定向输出使用 UTF-8，Agent 与原生适配器控制台都通过 Unicode API 显示中文。控制台不可写时仍保存日志并继续运行。保持 Agent 窗口运行，不要点击终端进入文本选择模式（旧 Windows 的快速编辑会暂停控制台输出）；睡眠或断网仍会真实离线。心跳独立于清单采集，短暂读取延迟不再导致错误离线。不要把同一配置同时用于两台网吧机器。连接密钥只在添加网吧时提供，妥善保管配置。

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

“已下载”表示 PCStory 的本地记录为状态 1 且目录存在，不进行所有游戏文件的完整校验。数据库状态 2/3/5/6 表示排队或暂停，不能据此判断实时状态。独立进度线程每 1 秒读取 PCStory 下载列表全部单元格，网页每 1 秒更新状态、两位小数百分比、实际下载速度、更新量、剩余量及预计时间；展开“下载器全部信息”查看原始字段。网络正常时通常有约 1～2 秒采样与显示延迟，PCStory 自己的刷新间隔也影响结果。只读采样不点击或切换页面。暂停保留实际百分比并停止预计时间；未知字段显示未知，采样超过 8 秒隐藏实时速度与 ETA，状态显示等待更新。完成仍由后续库存确认，不把 100% 等同安装完成。

进度诊断写入 Agent 同目录 pcstory-progress.json，包含列标题与匹配 GID 的原始单元格。更新时需同时替换 Agent.exe 和 Server.exe，保留配置、SQLite 数据库与任务日志。CI 验证实际跨进程的隐藏标准列表读取、HTTP 上报和网页显示；真实 PCStory 能否返回其绘制字段仍需测试机验证。

仅适配已经验证的 PCStory SHA256 `05b9927164f7b3a842f48ffc3bcfd464ae4052e2cdeec4f54902925f2178cdb6`。不同版本停止库存和命令操作。GID 8049 在此版本具有特殊强制更新逻辑，适配器拒绝执行。

网页支持暂停、继续和删除下载任务。控制按 GID 定位标准列表的唯一选中行，发送 PCStory 下载页已核对的原生菜单命令，随后读取状态确认；不依赖鼠标坐标，操作会短暂改变原生选择并尝试恢复。删除任务调用 0x8018，不调用“删除游戏”；部分或临时文件的清理由 PCStory 决定。本地手工暂停自动同步，本地删除须有效列表连续三次、至少两秒确认缺失后解锁重下发；读取失败不认定删除，新增任务有 15 秒启动宽限。网页“移除记录”仅隐藏结束的后台记录。

控制日志 agent-control.json 持久化 prepared/executing/result；中断后 executing 只上报结果不确定，不自动重复发送。操作失败或待确认可查看当前本地状态再发起新操作；结果显示为 confirmed 才表示读取了预期状态。磁盘检查不预留其他并发任务的空间，PCStory 最终处理容量和它自己的保留空间设置。

当前是单服务进程版本；库存或连接超过有效期停止下发。已完成本地测试和 GitHub 构建验证后仍需测试机验证自动读取与网页下载整个流程。Python 3.8 构建兼容旧系统，但旧 Windows 的完整运行兼容性以实际测试为准。

## 开发验证

```sh
python -m pip install cryptography==42.0.8
python -m unittest discover -s prototype -p 'test*.py'
```

cryptography 只用于独立密码算法测试，不属于 Agent 或服务端运行依赖。Actions 还验证冻结 EXE 运行、原生 IPC、退出码、Windows 跨进程进度采样与 Windows 7 静态导入。
