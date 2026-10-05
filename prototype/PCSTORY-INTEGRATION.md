# PCStory 适配器接入说明

现有精灵组件已经把下载器统一成任务模型。静态分析确认的关键入口包括：

- `batch-download-game`
- `DownloadGamePartialAsync`
- `StartTasksDownload`
- `ViewerGetDownloadingList`
- `CleanPcstoryAsync`
- `CenterToCafe_Download_SYN_Forward`
- `CafeToCenter_Download_ACK`

网页服务只负责发送任务，Windows Agent 负责把任务转换为本机适配器调用：

```text
{ gameId, forceUpdate, disk, downloader }
        ↓
PcstoryAdapter
        ↓
GDP_Agent_Ext / PCStory 本地调用
```

暂不向 `9927`、`12001` 或 `13001` 直接发送未知二进制数据。真实适配器应优先复用 GDP_Agent_Ext 已有调用链，并通过日志或状态接口确认结果。
