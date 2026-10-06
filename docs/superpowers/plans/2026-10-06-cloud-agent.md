# 云端网页与网吧 Agent 实现计划

目标：交付无需在测试机安装 Python 的 EXE，以及可部署的云端网页服务。

架构：服务端保存网吧、任务及库存；每家网吧使用独立凭证主动轮询。Agent 认证只读 PCStory 数据库页，读取实际下载盘配置，调用已经验证的 native 适配器。网页展示真实任务结果，不生成进度百分比。

1. `prototype/inventory.py`：标准库实现已验证的页认证和临时快照，拒绝活动 WAL、损坏页、不同程序哈希。通过独立密码实现测试和提供的数据库验证。
2. `prototype/server.py`：凭证隔离、持久化、清单有效期、容量检查、任务去重、已下载记录完成任务。通过真实 HTTP 集成测试验证。
3. `prototype/web.html`：中文登录、网吧添加及配置下载、改名、搜索、磁盘空间和任务列表。浏览器测试验证登录到下发流程。
4. `prototype/run_agent_config.py`：从 EXE 目录读取配置，解析相对路径，保留本地日志和启动失败信息。
5. `.github/workflows/build-agent.yml`：Windows x64 打包两个 EXE，包含网页和 native 适配器；运行冻结程序、IPC 和导入检查后输出 ZIP。
6. `deploy/` 与中文说明：本机单台 Windows 测试步骤；Linux Docker + HTTPS 部署步骤。云服务器尚未租用，交付不声称已经部署。

交付前运行所有测试、代码审查、GitHub 实际编译并下载校验产物。Windows PCStory 的清单上报及网页触发下载需要用户测试机最终验证。
