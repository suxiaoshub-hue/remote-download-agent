# Linux 服务端

Linux 只运行网页/API 服务端；各网吧继续运行 Windows Agent.exe 和 PCStory。

推荐 Ubuntu 22.04/24.04 或 Debian 12，安装 Docker Engine 与 Compose 插件，域名解析到云服务器，放行 80/443。无需在 Linux 安装 PCStory。

```sh
git clone https://github.com/suxiaoshub-hue/remote-download-agent.git
cd remote-download-agent/deploy
cp .env.example .env
```

修改 `.env` 的 `DOWNLOAD_DOMAIN` 为自己的域名，然后：

```sh
docker compose up -d --build
docker compose exec server cat /data/server-config.json
```

用输出的 adminToken 登录 https://你的域名。添加网吧时填写这个 HTTPS 地址，将配置放在对应网吧 Agent.exe 同目录。Caddy 自动管理证书；服务器 8765 端口不直接暴露公网。

更新服务：`git pull` 后 `docker compose up -d --build`。查看服务：`docker compose logs --tail=100 server`。不要执行 `docker compose down -v`，否则会删除数据库和密钥。

备份 server-data 卷中的 server-config.json、remote_download.db；恢复时须同时保留密钥和数据，避免重新添加网吧。

GitHub `Test Linux Server` 会在 Ubuntu 上运行接口测试、构建 Docker 镜像并实际验证容器重启后网吧数据保留。未租用服务器前仍可在 Windows 单机测试，稍后只需将 Agent 配置的 server 改为云端 HTTPS 地址。
