# 第三阶段：日历与自动化

## ICS

每场招聘会生成一个稳定 UID 的 VEVENT，不按企业或岗位拆事件。支持 Asia/Shanghai VTIMEZONE、中文 UTF-8、RFC 5545 字符转义、75 字节行折叠、CRLF、明确 DTSTART/DTEND、STATUS:CANCELLED 和可配置提醒。招聘会 revision 作为 SEQUENCE，改期保持同 UID。日历描述含来源与最多 20 个相关岗位摘要。

时间缺失、结束早于开始、未知状态或未经核实状态不导出，在 run.json 的 ics_skipped 列原因。私人活动不能进入公共日历。取消源记录有旧的明确时间时可输出取消事件；从未明确过时间则不会造时间生成占位事件。

## Google 同步

默认 SyncEngine 只生成计划，不读取 OAuth、访问 Google 或写映射。真实操作需 CLI `--enable-real-sync` 与环境变量 `ENABLE_REAL_CALENDAR_SYNC=true` 同时满足。还需要真实来源数据库、专用日历 ID、可信状态与明确区间。synthetic/unverified 禁止写入真实日历。

事件 ID 使用符合 Google 字符集的确定性招聘会 ID 哈希。扩展属性保存应用 owner、fairId、provenance、内容哈希和 revision；只有本应用持有的事件可更新/取消。本地映射以目标日历隔离，改期 PATCH 原事件。取消只删除已管理的、源明确取消的活动，缺失列表不会触发删除。

HTTP 请求有限次重试，尊重 429 Retry-After，代理/TLS/权限错误停止。成功远端操作后才写本地映射。创建成功但连接中断时，通过稳定 ID 查询恢复；POST 冲突 409 也核对所有权再恢复，避免重复创建。数据库丢失后同样可发现已有应用事件，禁止覆盖同 ID 的他人事件。

当前只通过模拟 API 和脚本化 HTTP 测试，没有 Google 授权，也没有真实同步成功的证据。

## Actions

- `tests.yml`：Python 3.10/3.12 矩阵，ruff、全部测试、离线 CLI、独立 JSON/CSV/ICS 校验，始终上传合成验证产物。
- `phase1-probe.yml`：保留第一阶段手动匿名探测，不定时调用学校接口。
- `recruitment.yml`：UTC `0 4,10,22 * * *`，对应北京时间 12:00、18:00、次日 06:00；GitHub 调度可能延迟。

定时任务使用真实模式，但 `ENABLE_REAL_COLLECTION` 默认为 false，因此只留下清晰 skip 原因。手动触发默认 offline，支持窗口、指定 ID、报告开关、日历 dry-run。启用真实采集还需已核实的映射文件。定时任务始终不会触发真实 Google 写入；只有手动明确选择真实同步并满足额外开关/Secrets，才进入 Google 步骤。

状态使用独立 offline/real cache。缓存备份仅保存经过脱敏的公开招聘模型/历史，不含 calendar_map、私人日程或 OAuth；原始运行数据库位于 work/，不上传 artifact。Google 映射在本地/self-hosted 数据库完整持久化；托管 runner 使用稳定远端 ID 恢复映射。Actions cache 会被淘汰，不能当作永久备份，需长期历史时使用 self-hosted 持久存储并备份。

工作流只读仓库权限；输入经环境变量传入 Python 参数列表，没有拼接到 shell。OAuth Secrets 只绑定手动真实同步步骤。采集失败停止当前操作并保留失败摘要；没有无限重试和代理绕过。
