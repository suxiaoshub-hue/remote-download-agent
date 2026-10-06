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

`status` 只允许 `installed`、`missing`、`downloading`、`unknown`。
Agent 读取清单中的 `LocalPath`，并检查路径是否存在；数据库有记录但
路径不存在时上报 `missing`。没有本地路径时上报 `unknown`，不把数据库
目录记录误报为已下载。

查询：

```text
GET /api/cafes/{cafeId}/inventory?query=roblox
```

返回匹配的游戏和该网吧最近一次上报的磁盘空间。网页已经使用这个接口
显示状态和可用空间，只有 `installed` 行显示“已下载”，其他状态提供下发
按钮。

Agent 配置：

```json
{
  "inventoryFile": "games.json",
  "diskPaths": ["D:\\"],
  "inventoryInterval": 30
}
```

如果 `diskPaths` 留空，Agent 会从本地游戏路径自动推断 Windows 盘符。
