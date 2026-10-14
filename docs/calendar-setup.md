# 指定 Google Calendar 服务账号同步、OAuth 与 Apple 显示

无人值守首选服务账号，详见 [完整服务账号配置](google-calendar-service-account.md)。GitHub **Variables** 使用 GOOGLE_CALENDAR_ID，**Secrets** 使用 GOOGLE_CREDENTIALS。默认离线 dry-run；只读授权预检是 calendar-access.yml，定时同步是 google-calendar.yml。没有真实凭据时仍能完成离线计划，不能称为已授权或同步成功。

## 无授权：先使用 ICS

`campus-manager run --mode offline` 会生成 reports/offline/recruitment.ics。用 Apple 日历“文件 → 导入”选择此文件，建议导入专门的测试日历。这里的文件包含合成样本，不是学校活动。真实采集验证后再导入真实导出。

重复手工导入在不同客户端可能生成重复副本，稳定 UID 不能保证所有导入器的去重行为。生产用途优先通过同一个 Google 日历账户显示，或将人工审核过的公开 ICS 放在受控 HTTPS 地址并订阅同一地址。当前没有发布订阅网站，也没有自动创建真实日历。

## 本地 OAuth 授权（需你主动执行）

1. 在自己的 Google Cloud 项目启用 Calendar API，配置 OAuth 同意屏幕；如处于 Testing，添加自己为测试用户。Testing 的 refresh token 生命周期可能有限，按 Google 当前政策处理。
2. 创建 Desktop app OAuth client，将下载文件存于 ignored private/client-secret.json，设置 chmod 600，不提交 Git。
3. 安装 `.[calendar]`，在本地私有终端显式运行：

```bash
chmod 600 private/client-secret.json
.venv/bin/campus-manager authorize --client-file private/client-secret.json --token-file private/google-token.json
```

程序仅在这个命令中启动本地回调和授权浏览器。请求 calendar.events 范围、offline consent，Token 文件以 600 权限创建，不覆盖已有 Token。当前开发过程没有执行该命令，没有访问真实账户授权。

4. 在 Google Calendar 手动创建专用“校招”日历，获取 Calendar ID。先运行 dry-run，检查缺失时间、可信状态和事件数量：

```bash
.venv/bin/campus-manager sync --db state/real.db --calendar-id YOUR_CALENDAR_ID --output-dir reports/calendar-plan
```

5. 只有你明确准备写入真实活动时，设置环境变量并运行：

```bash
export ENABLE_REAL_CALENDAR_SYNC=true
.venv/bin/campus-manager sync --db state/real.db --calendar-id YOUR_CALENDAR_ID --token-file private/google-token.json --enable-real-sync --output-dir reports/calendar
```

Token 不作为命令行字符串传入、不打印。OAuth refresh 自动在授权失效时尝试；失败仅输出通用提示。真实模式拒绝 synthetic/unverified 数据。开始和结束时间缺失时不会创建。明确取消源活动只取消本应用已管理事件。

## GitHub Secrets

优先在 Repository Secrets 中设置 `GOOGLE_CREDENTIALS`（服务账号 JSON），在 Repository Variables 中设置 `GOOGLE_CALENDAR_ID`。OAuth 兼容路径仍使用 Secret `GOOGLE_OAUTH_TOKEN_JSON`。在 GitHub UI 的私有 Secrets 表单输入凭据，绝不提交文件或粘贴到公开 Issue/PR。

真实采集仍需 ENABLE_REAL_COLLECTION 和核实的字段映射。独立日历同步需 ENABLE_REAL_CALENDAR_SYNC=true 且手动勾选 enable_real_sync；定时真实同步还需新增 ENABLE_SCHEDULED_CALENDAR_SYNC=true。不要为了测试 dry-run 开启这两个写入开关。只读 calendar-check 不需要写入开关。

## Apple 显示 Google 日历

在 macOS/iOS 的系统账户设置中添加自己的 Google 账户，启用日历，选择专用“校招”日历显示。应用写的是 Google Calendar API，Apple 显示同一 Google 日历；没有直接调用 iCloud API。设备显示是否成功需要你在授权后实际确认，本任务不声称已同步 Apple 或 Google。

数据库以日历 ID 哈希隔离映射，旧的明文键自动迁移。同步工作流的脱敏缓存保留哈希映射，失败时也保留已成功的映射；缓存丢失可从稳定事件 ID 与系统所有权标记恢复。报告不包含私钥、Token 或其他日程。取消已有事件属于账号写操作，默认 dry-run 避免意外发生。
