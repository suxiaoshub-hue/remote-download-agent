# Windows 构建

GitHub Actions 工作流 `.github/workflows/build-agent.yml` 使用 Python 3.8 和 PyInstaller 5.13.2。网吧不用安装 Python。

输出 ZIP 包含 Agent.exe、Server.exe、PcstoryAdapter.exe、启动 CMD、配置示例和中文说明。网页使用 PyInstaller --add-data 内嵌服务端。

构建时执行全量单元/HTTP 测试，再运行冻结 Server.exe 和 Agent.exe，验证网页、凭证、自动心跳、清单读取错误和本地日志。原生适配器另做 Windows 7 导入审查和跨进程消息测试。

配置须从网页添加网吧时下载；不要简单改名示例后运行。详见 README.md。
