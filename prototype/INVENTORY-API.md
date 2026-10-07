# API

所有 API 使用 Authorization: Bearer TOKEN。网页管理员使用 server-config.json 的 adminToken；每家 Agent 使用添加网吧时生成的 agentToken。Agent 凭证仅操作对应网吧。网页根路径公开，接口均有认证。

管理员：GET /api/state；POST /api/cafes（name/server，返回专属配置）；POST /api/cafes/{id}/rename；GET /api/cafes/{id}/inventory?query=名称或编号；POST /api/tasks（cafeId/gameId）；POST /api/tasks/{id}/cancel（仅 queued）。

Agent：POST /api/agents/register、heartbeat、inventory、error，均带 cafeId；GET /api/tasks/next/{cafeId}；POST /api/tasks/{id}/status。

下载进度：Agent 只有在能读取实际下载目录字节变化时，才 POST /api/tasks/{id}/telemetry，字段为 downloadedBytes、totalBytes、speedBytesPerSecond、sampledAt、source（filesystem 或 pcstory-log）。服务端校验任务所属网吧、总量、时间新鲜度和来源；剩余时间按（总量−已下载量）/真实速度向上取整。没有有效样本时 etaSeconds 保持 null，网页显示“剩余时间等待真实进度”。

inventory 包含 complete、games 和 disks。游戏字段 gameId、name、status、localPath、sizeBytes、localVersion、serverVersion。状态 installed/not_installed/missing/pending/unknown。磁盘字段 path/freeBytes/totalBytes/downloadDisk。服务端赋更新时间；心跳超过 75 秒、库存超过 120 秒或读取报错即禁止下载。独立心跳每 5 秒发送，不被清单读取阻塞。GET /api/cafes/{id}/overview 只返回清单更新时间、有效性和磁盘容量，不传全部游戏。

领取任务：queued → delivering，同一未确认任务可重新领取；Agent 写入本地 prepared 记录后确认 accepted，写 executing 后才调用原生程序。结果存储后重试上传。Agent 重启遇到 executing 改为 uncertain，避免重复执行。后续真实库存的 installed 记录可确认 completed。相同状态上传幂等。任务字段 progress、downloadedBytes、totalBytes、speedBytesPerSecond、etaSeconds 和 telemetryUpdatedAt 均来自有效进度样本，不用固定倒计时。

未下载判断来自 PCStory 完整目录内的明确记录，不能将没有库存或失效清单当成未下载。磁盘只上报 PCStory 配置指定盘。接口拒绝重复活动任务、已下载/未知状态及空间不足。
