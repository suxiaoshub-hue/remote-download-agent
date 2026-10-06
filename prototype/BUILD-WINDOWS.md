# Windows EXE 构建

构建机只需要安装一次 Python，网吧测试机和正式网吧不需要安装 Python。

在 Windows 构建机执行：

```bat
build_windows_agent.bat
```

输出文件：

```text
dist\Agent.exe
```

Agent 配置增加了库存上报项：

```json
{
  "inventoryFile": "games.json",
  "diskPaths": ["D:\\"],
  "inventoryInterval": 30
}
```

`games.json` 使用 `PcstoryReader` 导出的文件。Agent 不修改 PCStory 数据库，
只读取清单中的游戏路径并检查是否存在，再上报服务器；磁盘容量使用
Windows 的磁盘空间接口读取。云端网页通过 `/api/cafes/{id}/inventory`
搜索指定网吧的状态。

正式部署目录包含：

```text
Agent.exe
agent-config.json
PcstoryAdapter.exe
pcstory-adapter.ini
```

新版 GitHub 构建会把 `PcstoryAdapter.exe` 一起放进发布 ZIP，Agent 的
`pcstoryCommand` 默认直接调用同目录适配器；不需要另外复制下载程序。
