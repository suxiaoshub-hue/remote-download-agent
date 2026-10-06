# 网吧库存接口

Agent 注册后，定期向 `POST /api/agents/inventory` 发送当前快照：

```json
{
  "cafeId": "cafe-001",
  "games": [{
    "gameId": 5131,
    "name": "Roblox",
    "status": "installed",
    "localPath": "D:\\Games\\Roblox",
    "localVersion": 1,
    "serverVersion": 1,
    "sizeBytes": 1200
  }],
  "disks": [{
    "path": "D:\\",
    "freeBytes": 9000,
    "totalBytes": 10000
  }]
}
```

`status` 只允许 `installed`、`missing`、`not_installed`、`downloading`、`unknown`。
Agent 读取清单中的 `LocalPath`，并检查路径是否存在；数据库有记录但
路径不存在时上报 `missing`。没有本地路径时上报 `unknown`，不把数据库
目录记录误报为已下载。

查询：

```text
GET /api/cafes/{cafeId}/inventory?query=roblox
```

返回匹配的游戏和该网吧最近一次上报的磁盘空间。服务器会把中央游戏目录
和网吧库存合并：目录中存在、但该网吧库存没有的游戏显示为 `not_installed`
（网页显示“未下载”）。从未上报库存的网吧返回 `reported=false` 和空游戏列表，
避免把离线网吧误报为未下载。网页只有 `installed` 行显示“已下载”，其他状态
提供下发按钮。

Agent 配置：

```json
{
  "inventoryFile": "games.json",
  "diskPaths": ["D:\\"],
  "inventoryInterval": 30
}
```

如果 `diskPaths` 留空，Agent 会从本地游戏路径自动推断 Windows 盘符。
