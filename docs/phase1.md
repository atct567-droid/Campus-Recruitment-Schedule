# 第一阶段：验证范围、证据和备选部署

本页保留 2026-10-14 第一阶段探测时的历史状态。后续离线采集管理、报告、ICS 与同步适配器已实现，最新实现及验证状态见 README 和 final-delivery-report.md；历史学校响应仍未验证。

## 验证范围

| 接口 | 参数/路径 | 实现 | 本次真实验证 |
| --- | --- | --- | --- |
| `/api/view/jobFair/list` | `pageNum`、`pageSize`、可选 `calendarStartDate` | HTTP/业务 code/JSON/全分页 | 代理 CONNECT 403，未获得学校响应 |
| `/api/view/getJobFair/{jobFairId}` | 从真实列表发现 ID，或显式传入真实 ID | HTTP/业务 code/非空 data 对象 | 未测试 |
| `/api/view/jobFair/positionList` | `jobFairId`、`pageNum`、`pageSize` | HTTP/业务 code/JSON/全分页 | 未测试 |
| `/api/view/position/companyGroupList` | `jobFairId`、`pageNum`、`pageSize` | HTTP/业务 code/JSON/全分页 | 未测试 |

实现和离线测试通过，不代表上述接口可匿名访问。空列表是有效的列表结果，但不能用于确认详情/企业/岗位接口可用；只有实际测试的接口才标为 `verified`。

## 云端失败证据

北京时间 2026-10-14 01:22:50，第一个请求为：

```text
GET https://bysjyw.guat.edu.cn/api/view/jobFair/list?pageNum=1&pageSize=50
ProxyError: Tunnel connection failed: 403 Forbidden
kind=proxy_policy
http_status=null
requests_attempted=1
retry=false
```

此 403 来自代理隧道建立过程，不是学校 API 的 HTTP 状态。云环境配置为 restricted，学校域名不在读取到的允许访问列表，VPN 未配置。环境状态工具的策略状态为 `unknown`，不能仅凭配置快照声称策略已生效；实际 CONNECT 拒绝是本次连接失败的直接证据。程序停止，未尝试登录、切换 IP、关闭代理、关闭 TLS 校验或绕过限制。

完整证据：

- [逐请求日志](evidence/cloud-2026-10-14/requests.jsonl)
- [JSON 验证报告](evidence/cloud-2026-10-14/verification.json)
- [Markdown 验证报告](evidence/cloud-2026-10-14/verification.md)
- [网络策略摘要](evidence/cloud-2026-10-14/environment.json)
- [离线测试日志](evidence/cloud-2026-10-14/tests.txt)

## 日期查询如何验证

默认查询不带日期的列表，以及锚点月初、锚点前一天、当天、后一天和 30 天后；每个查询都会处理全部分页。原始查询及结果保存在 `verification.json`。探测日期不等于“未来 30 天采集窗口”。

得到真实响应后，人工核对招聘会 ID、实际开始日期字段，以及字符串日期是否按北京时间表示。传入 `--id-field` 和 `--date-field` 后，程序还会选一个有开始日期的招聘会，测试开始日前一天、当天、后一天。带时区的 ISO 时间转为 Asia/Shanghai；不猜测数值时间戳和未知格式。

工具比较当天筛选、包含/不包含起始日的下界筛选、月份筛选、忽略参数五个假设。没有日期字段、有效样本不足、结果全部为空/完全相同、ID 重复、同一 ID 日期变化或任何查询失败时，保持 `unverified`。仅一个假设符合本次全部观察时标记 `observed_evidence`，不能由此推断永久接口契约。

第二阶段真实采集启用前还应复核：同日多场、跨月/跨年、范围前后边界，检查不带日期的列表是否也有默认过滤。确认接口语义之后才能决定逐日/逐月查询或按起始日期查询并本地过滤；不能先假定 `calendarStartDate` 是“从当天起”。第一阶段探测时没有未来 30 天报告或日历同步；后续离线链路已实现，但仍没有真实学校全量报告或真实同步证据。

## 备选部署方法

### 本地电脑或可合法访问学校网站的服务器

优先在能正常打开学校页面的本地网络运行同一代码，不更改 TLS 和代理安全设置。安装/测试命令见 README。先不传日期字段生成真实验证报告；依据其 `queries[].collection.rows` 核对字段，再使用日期字段复跑。

```bash
.venv/bin/campus-recruitment --output-dir reports/local-phase1
.venv/bin/campus-recruitment --date-field ACTUAL_START_DATE_FIELD --output-dir reports/local-phase1-with-dates
```

这两个示例命令是部署步骤，尚未在该网络执行。退出码非零时保留全部报告，不将失败当成“学校没有招聘会”。若学校返回 `401/403/429` 或登录跳转，应停止并遵守其访问政策；不要通过换 IP 或加伪造凭据规避限制。

### GitHub 托管 runner 手动验证

提交本项目后，在 Actions 中手动运行 `Phase 1 API probe`。该工作流不使用密钥，无 `schedule`，只执行匿名验证。每次运行保存 `phase1-evidence-*` artifact，失败时也上传报告；`probe` 步骤非零将使任务失败。GitHub runner 是否能连接学校也需实际验证，不能预先保证。

可选输入 `date_field`、`id_field`、真实 `job_fair_id`；工作流把输入放到环境变量，再以参数数组传给 Python，避免将输入直接拼接成 shell 命令。日期未填写时使用北京时间当天。

### 校内/本地 self-hosted runner

如果 GitHub 托管 runner 连不上，可在学校允许访问的校内/本地机器注册一个带 `guat-network` 标签的 self-hosted runner，将手动探测工作流中的 `runs-on: ubuntu-latest` 改为：

```yaml
runs-on: [self-hosted, linux, x64, guat-network]
```

只给可信仓库使用该 runner，保持相同限速与失败行为。此方法使用获准的网络运行，并不绕过学校拒绝访问的策略。

### 当前云环境按正常配置流程添加域名

可在云环境网络配置中允许 `bysjyw.guat.edu.cn`，经配置生效后重新启动或复查环境，再原样重跑验证命令。不要编辑 `/etc/codex/network-policy.json` 试图授权；该文件只是启动快照。此处没有修改云配置，也没有尝试策略绕过。

## 离线验证覆盖

离线测试通过脚本化 requests Session 和真实 PreparedRequest 验证状态码/业务 code、匿名请求不发送 Session/netrc 凭据、保留代理和 TLS、超时及重试、Retry-After、全分页、服务端页大小限制、空结果、重复分页、分页变化、预算停止、四个路由、CLI 失败报告、日期证据不足和边界假设。测试不访问学校，不替代真实接口验证。

## 后续阶段约束

第一阶段真实契约确认后，再实现 ID 去重、未来 30 天窗口和新增/取消/改期检测。分页或网络失败的快照不能用来判定招聘会取消。目标岗位包括硬件、嵌入式、PCB、STM32、FPGA、电子工程师；需要实际岗位字段后再建立筛选规则。

第三阶段使用 Google Calendar API 将活动写入 Google 日历，Apple 日历通过添加 Google 账户显示该日历。每场双选会以稳定招聘会 ID 对应一个主事件；不明确的开始/结束时间不得自动创建。凭据仅放 GitHub Secrets 或私有本地文件。这些第一阶段后续约束已在当前离线 ICS、同步适配器和定时安全框架中实现，尚无真实授权或同步。
