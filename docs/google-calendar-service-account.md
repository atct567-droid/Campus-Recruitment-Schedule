# Google Calendar 指定共享日历无人值守同步

本功能已实现：本地 223 项测试通过，GitHub Python 3.10/3.12 矩阵均实际通过 223 项、ruff、离线 pipeline 与格式验证。权限预检和同步逻辑仍只通过 Mock 验证；开发环境没有 GOOGLE_CREDENTIALS，没有真实 Google 权限/写入验证，不声称真实同步成功。在线 CI 证据见 [github-status.json](evidence/service-account-calendar/github-status.json)。

## 配置约定与写入边界

| 配置 | 存放 | 用途 / 默认 |
| --- | --- | --- |
| GOOGLE_CALENDAR_ID | GitHub Actions **Variables** | 用户共享的指定日历 ID；不允许 primary 别名 |
| GOOGLE_CREDENTIALS | GitHub Actions **Secrets** | 服务账号下载的完整 JSON；从环境读取，不写凭据文件 |
| ENABLE_REAL_CALENDAR_SYNC | Variables | 默认 false；所有真实写入的总开关 |
| ENABLE_SCHEDULED_CALENDAR_SYNC | Variables | 默认 false；仅在已经确认后启用定时写入 |

时间统一 Asia/Shanghai。API 使用 calendar.events scope；程序只构造指定 ID 下的 events 请求，不列出/发现其他日历，也不读取其日程。若 --calendar-id 与环境中 GOOGLE_CALENDAR_ID 不一致，直接拒绝，不发请求。一个适配器绑定一个目标 ID，不会退回 primary。

默认离线 dry-run 不解析凭据、不请求 Google、不更新映射。`--check-access` 是显式只读预检/远端计划选项，也不写事件。真实写入还需要 CLI `--enable-real-sync`；工作流只在明确的手动选择或双重定时开关开启后传入该参数。

## 1. 建立 Google Cloud 服务账号

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 选择或新建自己的项目。
2. 打开 APIs & Services → Library，搜索并启用 **Google Calendar API**。
3. 打开 IAM & Admin → Service Accounts，创建专用服务账号，例如 campus-recruitment-sync。
4. 这个服务账号不需要项目 Owner/Editor 角色，也不需要访问用户邮箱或开启 domain-wide delegation。本系统只访问用户主动共享的日历，不创建邀请参与者或模拟用户登录。
5. 在该账号的 Keys → Add key → Create new key 中选择 JSON。将下载文件直接保存到本地受保护位置，例如 ignored `private/service-account.json`，权限设为 600。不要保存到仓库根目录、聊天、Issue 或 PR。
6. 在服务账号详情中复制 client_email。它是之后共享日历时使用的 `...@...iam.gserviceaccount.com` 邮箱，不是 GOOGLE_CALENDAR_ID。

若组织策略禁止导出服务账号密钥，不要规避策略；由管理员提供获准的身份方案，当前程序不会伪造凭据。密钥被意外公开时应在 Google Cloud 撤销并重新生成，单纯删掉 Git 文件不足以撤销密钥。

## 2. 用户共享已经存在的指定日历

1. 在 Google Calendar 网页中打开要同步的专用日历的“设置与共享”。建议使用独立“校招”日历，保护其他个人活动。
2. 在“与特定用户或群组共享”中添加上一步的服务账号 client_email，选择“**更改活动 / Make changes to events**”。无需授予管理共享权限。
3. 到“集成日历 / Integrate calendar”复制**日历 ID**，例如 `...@group.calendar.google.com`。复制实际 ID，不使用日历标题、订阅 URL 或 primary。
4. 日历显示时区建议设置为 Asia/Shanghai；无论日历原有时区如何，程序都在事件 start/end 中显式指定 Asia/Shanghai。

如果 Workspace 管理员限制向服务账号或外部身份共享，需要管理员按正常政策允许。程序的权限预检失败会停止，不会登录用户账户、替换身份或绕过共享限制。

## 3. GitHub 配置

在仓库 Settings → Secrets and variables → Actions：

- **Variables → New repository variable**：名称 GOOGLE_CALENDAR_ID，值为指定日历 ID。
- **Secrets → New repository secret**：名称 GOOGLE_CREDENTIALS，值为完整服务账号 JSON。
- ENABLE_REAL_CALENDAR_SYNC 和 ENABLE_SCHEDULED_CALENDAR_SYNC 都保持 false 或不设置，先验证 dry-run。

不要将 JSON 作为 workflow_dispatch 输入，也不要用 echo/cat 输出 Secret。程序不记录凭据或 HTTP Authorization，不把私钥/Token 保存到 SQLite、缓存或 artifact；SDK 解析/刷新失败只输出通用提示。日历 ID 本身使用 Variables，但程序的结果文件只保存其哈希命名空间。

## 4. 先完成只读权限预检

工作流文件合并到默认分支后，Actions → **Google Calendar access check (read only)** → Run workflow。它只调用：

```text
GET /calendar/v3/calendars/{GOOGLE_CALENDAR_ID}/events
参数：maxResults=1, fields=accessRole,timeZone
```

这个请求仅返回当前身份的权限和时区，不返回 items，不读取其他日程正文，也不需要将共享日历添加到服务账号的 CalendarList。writer、owner、writerWithoutPrivateAccess 被视为可写；reader/freeBusyReader/未知/403/404 都不能进入写入流程。

报告 `calendar-access.json` 的 status=verified 表示这次实际只读预检成功。Mock 测试的 verified_mock 和离线计划的 not_checked 不代表真实权限。没有凭据、未共享或权限不足时，工作流失败但上传脱敏失败结果，不会创建任何事件。

本地等效命令（仅自己主动运行）：

```bash
export GOOGLE_CALENDAR_ID='实际日历ID'
chmod 600 private/service-account.json
.venv/bin/campus-manager calendar-check --credential-file private/service-account.json
```

不要在命令行内放私钥。当前开发过程没有执行上述真实预检，也没有真实私钥可用。

## 5. dry-run 与实际启用

没有凭据也能执行完整合成计划：

```bash
.venv/bin/campus-manager run --mode offline --db state/offline.db
.venv/bin/campus-manager sync --db state/offline.db --output-dir reports/calendar-plan
```

每个 create/update 计划含 event_preview：企业摘要、匹配岗位、地点、官网链接、明确时间，以及 `popup` 提前 **1440 分钟和 30 分钟**提醒。只生成一个招聘会主事件，不按 200 家企业拆分日程。合成数据明确标记 synthetic，禁止写入真实日历。

已经具备真实招聘数据库时，可执行带只读远端核对的计划：

```bash
.venv/bin/campus-manager sync --db state/real.db --credential-file private/service-account.json --check-access --output-dir reports/calendar-plan
```

查看日历 scope、access_check、actions 和 event_preview。确认目标、权限、所有权及时间正确后，**由你明确启用**：

```bash
export ENABLE_REAL_CALENDAR_SYNC=true
.venv/bin/campus-manager sync --db state/real.db --credential-file private/service-account.json --enable-real-sync --output-dir reports/calendar
```

这里是启用说明，不是开发过程已经执行的动作。真实请求仍会先做权限预检。未知时间、状态不可信、私有记录、synthetic/unverified 数据均不写入。

## 6. 无人值守 Actions

**Designated Google Calendar unattended sync**（google-calendar.yml）每天 UTC 22:00/04:00/10:00，即北京时间 06:00/12:00/18:00 运行。GitHub 的 cron 可能延迟，工作流必须先在默认分支存在才会定时生效。

- 默认只对 state-cache/real.db 生成 dry-run；没有数据库时明确 skipped，不请求 Google。
- 手动运行默认 enable_real_sync=false，可选择 check_access=true 只读检查；缺少凭据时仍保留离线计划。
- 真实手动写入：必须勾选 enable_real_sync，并设置 ENABLE_REAL_CALENDAR_SYNC=true。
- 真实定时写入：由用户确认后将 ENABLE_REAL_CALENDAR_SYNC、ENABLE_SCHEDULED_CALENDAR_SYNC **同时设置为 true**。之后不需要每次点击确认，但每次运行都校验权限。
- 最新采集失败或不完整时暂停无人值守写入，保留现有日程；不会把旧数据库误标为新采集成功。
- 和招聘采集工作流共享 concurrency 锁，避免两个 runner 同时覆盖数据库。采集与日历使用相同公开状态缓存，独立同步可能使用上一轮完整快照，最新状态可在下次执行时同步；需要即时同步可按顺序手动运行采集和同步。

缓存保留 `(日历ID哈希, jobFairId) → event_id/内容哈希/revision/status`。旧明文键自动迁移，备份经过脱敏和 VACUUM，清除旧页残留。成功映射在单个操作后提交；后续请求失败时仍保存已成功的映射。缓存被 GitHub 淘汰时，通过稳定事件 ID 与系统 owner/fairId/provenance 标记恢复，不因此重复创建。

仅更新本系统拥有的事件。取消只针对源明确取消且远端有本系统所有权标记的主事件，不会遍历或删除其他日程。遇到其他来源的同 ID 事件直接停止。提醒属于 API 身份/客户端的设置，不能仅凭服务账号写入就保证所有共享用户或 Apple 设备实际收到提醒；启用后应在自己的设备核对，必要时配置该用户的日历提醒。

## 7. 验证证据与故障处理

当前证据位于 docs/evidence/service-account-calendar/：凭据优先级/私钥消息隐藏、指定日历绑定、权限预检、两条提醒、企业/岗位摘要、幂等改期/取消、缓存恢复、部分失败和默认安全开关都通过离线测试。没有真实 Google 授权/写入验证。

403/404：检查 Calendar API 启用、日历 ID、服务账号共享权限及组织策略。429/5xx：有限重试，尊重 Retry-After；超过等待上限停止。凭据格式错误或刷新失败：私下检查 Secret 和密钥状态，日志不会包含具体密钥。不要关闭 TLS、修改代理绕过限制或把 Token 粘贴到公开位置。

官方依据：[Events.list 权限元数据](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)、[Events 字段与提醒](https://developers.google.com/workspace/calendar/api/v3/reference/events)、[Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth)、[服务账号密钥管理](https://cloud.google.com/iam/docs/creating-managing-service-account-keys)。
