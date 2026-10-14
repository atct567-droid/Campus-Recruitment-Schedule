# Campus-Recruitment-Schedule

桂林航天工业学院校招信息系统，数据入口：<https://bysjyw.guat.edu.cn/index>。

Apple 日历可使用无需 Google Cloud 的 [ICS 订阅](docs/apple-ics-subscription.md)。固定地址：<https://raw.githubusercontent.com/atct567-droid/Campus-Recruitment-Schedule/ics-feed/recruitment.ics>。初始文件包含真实46场活动，自动更新工作流须合并到默认分支才生效。

已实现匿名客户端 → 配置标准化 → SQLite 去重与历史 → 岗位筛选 → 时间冲突 → 中文日报/周报和 JSON/CSV → ICS → Google Calendar dry-run/受控同步。学校四接口匿名访问已实际验证，未来30天目录取得46场；部分深分页受限，明细完整性单独标注。真实 Google 授权/写入仍未验证，定时采集和同步开关保持关闭。

## 完整离线运行

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test,calendar]'
.venv/bin/ruff check src tests scripts
.venv/bin/python -m pytest -q
.venv/bin/campus-manager run --mode offline
.venv/bin/python scripts/validate_outputs.py reports/offline
```

不需要学校网络或 Google 凭据。使用明确标为 synthetic 的合成场景，参考日期为 2026-10-14，得到 5 场活动、3 家企业、7 个岗位、4 个 ICS 事件；缺失时间的活动跳过。重复运行不会重复新增。数据库保存在 ignored state/offline.db，报告和日历位于 reports/offline/。`campus-recruitment` 保留原第一阶段探测入口，`campus-manager` 是完整系统入口。

文档：[架构与审计](docs/architecture.md)、[第二阶段](docs/phase2.md)、[第三阶段](docs/phase3.md)、[部署](docs/deployment.md)、[日历授权](docs/calendar-setup.md)、[排障](docs/troubleshooting.md)、[进度](docs/progress.md)、[最终交付报告](docs/final-delivery-report.md)。

指定 Google Calendar 无人值守同步优先支持服务账号共享日历：[完整 Google Cloud/GitHub 配置](docs/google-calendar-service-account.md)。GOOGLE_CALENDAR_ID 放 Actions Variables，GOOGLE_CREDENTIALS 放 Secrets。默认 dry-run，事件预览包含企业/岗位/地点/官网和提前 1 天、30 分钟提醒；只读权限预检与显式启用的定时写入分别有独立工作流。没有凭据也可完成离线链路。

## 本次验证结果

最新实际结果见 [学校公开访问说明](docs/school-public-access.md)。使用官网公开页面的 pageSize=10，无 Authorization/Cookie，四接口返回 HTTP 200/code 200；calendarStartDate 当前为当天筛选，2026-10-14 至11-12逐日取得46场活动。综合双选会企业第6页遭拒绝，因此公共预览有明确的明细范围限制，不能声称全量。下述 CONNECT 403 保留为早期云环境历史记录。

北京时间 2026-10-14 的真实云端探测，在第一个 `jobFair/list` 请求的 HTTPS CONNECT 阶段被代理拒绝：`Tunnel connection failed: 403 Forbidden`。未收到学校接口响应，`http_status` 为 `null`，没有可验证的业务 code 或 JSON。此结果不能证明学校接口拒绝匿名访问。

早期证据保存在 [docs/evidence/cloud-2026-10-14/](docs/evidence/cloud-2026-10-14/)，说明见 [第一阶段验证与部署文档](docs/phase1.md)。这些历史失败不代表最新采集状态。未创建或同步真实日历事件。

## 第一阶段探测

需要 Python 3.10+；以下命令适用于 Linux/macOS。Windows 使用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\campus-recruitment.exe`。

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/campus-recruitment --output-dir reports/phase1
```

默认日期取 Asia/Shanghai 的当天；可指定日期复现：

```bash
.venv/bin/campus-recruitment --anchor-date 2026-10-14 --output-dir reports/phase1
```

输出 `verification.json`、`verification.md` 和逐次请求日志 `requests.jsonl`。退出码 `2` 表示验证未完成，包括网络失败、未知响应结构、没有招聘会 ID、日期证据不足等情况；只有四个接口均通过验证、日期比较支持一个假设且无失败记录时才返回 `0`。报告明确区分接口验证与日期推断，`observed_evidence` 仅适用于本次样本，第二阶段前需要复核日期边界。

首次获得真实样本后，核对 ID 和招聘会开始日期字段，再运行：

```bash
.venv/bin/campus-recruitment --id-field jobFairId --date-field startTime --page-size 10 --public-page jobfairSearch --output-dir reports/phase1-with-dates
```

startTime 已从实际学校 JSON 核实。不可把发布时间或报名截止时间当作招聘会开始时间。不指定 --date-field 时，工具只记录日期查询响应，不推断其语义。

## 安全与完整性约定

- 默认只接受业务 code 200（整数或字符串），当前实际学校成功响应为整数200。其他值即失败；不要为消除报错而添加未知值。
- 每次请求（包括重试）默认至少间隔 2 秒，串行执行；不携带授权头、登录 Cookie 或 netrc 认证。保留系统代理和 CA 配置，TLS 校验始终开启，不跟随重定向。
- 默认连接/读取超时为 5/20 秒，最多重试 2 次。仅重试超时、连接失败和 HTTP `429/500/502/503/504`；遇到权限拒绝、代理策略拒绝、TLS 错误不重试。尊重 `Retry-After`，等待值超过 60 秒则停止，留待稍后运行。
- 支持顶层 `rows`、`data.rows` 和数组 `data`；读取顶层或 `data` 对象中的 `total/totalPage`。未知结构、冲突、提前空页、重复行或分页总数变化会失败，不返回伪完整数据。
- 有分页元数据时读至声明的末页或总数；无元数据时读到明确空页，不因短页提前停止。默认单次验证预算 200 次请求、单个列表上限 1000 页；触及上限标为未完成，可在明确了解数据规模后调整。
- 日志不记录请求认证头或响应 Cookie；异常中的代理 URL 凭据会脱敏。业务样本可能包含学校公开的联系信息，`reports/` 默认不入库。

GitHub Actions 包含测试矩阵、手动第一阶段探测、只读日历授权预检和北京时间 06:00/12:00/18:00 定时框架。真实采集默认关闭；独立日历定时任务默认 dry-run，真实自动写入需用户明确开启两个 Variables。手动写入仍需显式选择、专用日历和 Secrets。在线工作流的实际验证状态见交付证据，不能仅由配置文件推断成功。
