# Campus-Recruitment-Schedule

桂林航天工业学院校招信息系统，数据入口：<https://bysjyw.guat.edu.cn/index>。

已实现完整离线链路：匿名客户端 → 配置标准化 → SQLite 去重与历史 → 可解释岗位筛选 → 时间冲突 → 中文日报/周报和 JSON/CSV → ICS → Google Calendar dry-run/受控 OAuth 适配器。真实学校访问与 Google 授权仍未验证，真实采集和同步默认关闭。

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

## 本次验证结果

北京时间 2026-10-14 的真实云端探测，在第一个 `jobFair/list` 请求的 HTTPS CONNECT 阶段被代理拒绝：`Tunnel connection failed: 403 Forbidden`。未收到学校接口响应，`http_status` 为 `null`，没有可验证的业务 code 或 JSON。此结果不能证明学校接口拒绝匿名访问。

原始证据保存在 [docs/evidence/cloud-2026-10-14/](docs/evidence/cloud-2026-10-14/)，说明见 [第一阶段验证与部署文档](docs/phase1.md)。其他三个接口、日期查询语义均未验证，未采集招聘数据，未创建或同步日历事件。

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
.venv/bin/campus-recruitment --id-field jobFairId --date-field ACTUAL_START_DATE_FIELD --output-dir reports/phase1-with-dates
```

`ACTUAL_START_DATE_FIELD` 必须替换为响应中核实的字段名。不可把发布时间或报名截止时间当作招聘会开始时间。不指定 `--date-field` 时，工具只记录日期查询响应，不推断其语义。

## 安全与完整性约定

- 默认只接受业务 code `200`（整数或字符串），其他值即失败。这是待验证的客户端配置，不是对学校接口的已验证结论。若真实响应或官方说明确认另一成功 code，显式设置 `--success-code`；不要为消除报错而添加未知值。
- 每次请求（包括重试）默认至少间隔 2 秒，串行执行；不携带授权头、登录 Cookie 或 netrc 认证。保留系统代理和 CA 配置，TLS 校验始终开启，不跟随重定向。
- 默认连接/读取超时为 5/20 秒，最多重试 2 次。仅重试超时、连接失败和 HTTP `429/500/502/503/504`；遇到权限拒绝、代理策略拒绝、TLS 错误不重试。尊重 `Retry-After`，等待值超过 60 秒则停止，留待稍后运行。
- 支持顶层 `rows`、`data.rows` 和数组 `data`；读取顶层或 `data` 对象中的 `total/totalPage`。未知结构、冲突、提前空页、重复行或分页总数变化会失败，不返回伪完整数据。
- 有分页元数据时读至声明的末页或总数；无元数据时读到明确空页，不因短页提前停止。默认单次验证预算 200 次请求、单个列表上限 1000 页；触及上限标为未完成，可在明确了解数据规模后调整。
- 日志不记录请求认证头或响应 Cookie；异常中的代理 URL 凭据会脱敏。业务样本可能包含学校公开的联系信息，`reports/` 默认不入库。

GitHub Actions 包含测试矩阵、手动第一阶段探测和北京时间 06:00/12:00/18:00 定时框架。真实采集默认关闭；定时任务没有真实日历写入口。手动真实同步还需要双重启用开关、专用日历及 Secrets。在线工作流的实际验证状态见最终交付报告，不能仅由配置文件推断成功。
