# API

所有 API 使用 Authorization: Bearer TOKEN。网页管理员使用 server-config.json 的 adminToken；每家 Agent 使用添加网吧时生成的 agentToken。Agent 凭证仅操作对应网吧。网页根路径公开，接口均有认证。

管理员：GET /api/state；POST /api/cafes（name/server，返回专属配置）；POST /api/cafes/{id}/rename；GET /api/cafes/{id}/inventory?query=名称或编号；POST /api/tasks（cafeId/gameId）；POST /api/tasks/{id}/cancel（仅 queued）。

Agent：POST /api/agents/register、heartbeat、inventory、error、progress-error，均带 cafeId；GET /api/tasks/next/{cafeId}；GET /api/tasks/active/{cafeId}；POST /api/tasks/{id}/status。active 只返回本店需要采样的任务，不重复下发命令。

下载进度：独立线程每 1 秒读取 PCStory 标准下载列表，按列标题与 GID 匹配，不点击或切换页面。POST /api/tasks/{id}/telemetry 字段为 progress（0～1 或 null）、downloadState、remainingBytes、updateBytes、speedBytesPerSecond、sampledAt、source（固定 pcstory-listview）、listFields（title/value 数组，最多 24 列）。downloadState 为 downloading/paused/waiting/checking/completed/failed/unknown/absent。服务端校验任务所属网吧、数值范围和样本时间；只在正在下载且剩余量与速度均大于零时按 remainingBytes/speedBytesPerSecond 计算 ETA。暂停保留列表实际百分比，未知字段保留 null。清单状态 pending 不推断实时暂停或下载状态。

GET /api/state 的 progressFresh 要求来源有效、Agent 在线、采样和上报均不超过 8 秒。旧样本保留百分比并标注上次读取，ETA 与当前速度隐藏，displayState 为 unknown。PCStory 实际列表原始标题、目标行与采集错误写入 Agent 同目录 pcstory-progress.json；读取失败单独上报 progressError，不阻塞心跳或清单。Agent 重启根据 active 重新采样已有任务。有效控件连续三次、至少两秒缺失后状态为 removed，解除重复 GID 锁；新增未观察到的任务有 15 秒启动宽限。

管理员 POST /api/tasks/{id}/control（action=pause/resume/remove），要求在线且新鲜的实际状态，重复执行中操作返回 409。Agent GET /api/controls/next/{cafeId}、POST /api/controls/{id}/status（accepted/confirmed/failed/uncertain），凭证绑定店铺；confirmed 必须携带匹配操作的真实 sample。操作队列持久化；Agent 本地 agent-control.json 防止重启重复执行。原生列表按 GID 唯一选中发送 WM_COMMAND 0x8016/0x8017/0x8018，再读状态确认。POST /api/tasks/{id}/dismiss 仅隐藏终态网页记录；POST cancel 只取消 queued。

inventory 包含 complete、games 和 disks。游戏字段 gameId、name、status、localPath、sizeBytes、localVersion、serverVersion。状态 installed/not_installed/missing/pending/unknown。磁盘字段 path/freeBytes/totalBytes/downloadDisk。服务端赋更新时间；心跳超过 75 秒、库存超过 120 秒或读取报错即禁止下载。独立心跳每 5 秒发送，不被清单读取阻塞。GET /api/cafes/{id}/overview 只返回清单更新时间、有效性和磁盘容量，不传全部游戏。

领取任务：queued → delivering，同一未确认任务可重新领取；Agent 写入本地 prepared 记录后确认 accepted，写 executing 后才调用原生程序。结果存储后重试上传。Agent 重启遇到 executing 改为 uncertain，避免重复执行。后续真实库存的 installed 记录可确认 completed，单独 100% 样本不能确认安装完成。相同状态上传幂等。实时 progress、remainingBytes、speedBytesPerSecond、etaSeconds 和 telemetryUpdatedAt 来自下载列表样本，不按文件长度或游戏目录总大小推算。

未下载判断来自 PCStory 完整目录内的明确记录，不能将没有库存或失效清单当成未下载。磁盘只上报 PCStory 配置指定盘。接口拒绝重复活动任务、已下载/未知状态及空间不足。
