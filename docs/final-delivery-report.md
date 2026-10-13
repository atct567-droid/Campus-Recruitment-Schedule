# 校招系统第二、第三阶段最终交付报告

## 实现与验证状态

| 能力 | 已实现 | 离线/本地验证 | 真实在线验证 |
| --- | --- | --- | --- |
| 第一阶段匿名请求与分页 | 是，最小扩展记录数上限 | 原有 103 项保留通过 | 学校 HTTP/JSON 未收到，历史 CONNECT 403 来自代理 |
| 标准化/日期窗口/ID 去重/SQLite 历史 | 是 | 合成多页、空数据、异常、重复执行、改期/取消通过 | 真实字段与日期语义未验证 |
| 岗位筛选/冲突/报告/JSON/CSV | 是 | 可解释误匹配、跨日、半开区间、隐私隔离通过 | 没有真实学校数据报告 |
| ICS | 是 | 独立 icalendar 解析、中文/转义/75 字节/时区/改期/取消通过 | Apple 客户端导入/订阅未实机验证 |
| Google Calendar | 是，默认 dry-run | 模拟幂等、改期、取消、断线、429、401、409、所有权通过 | 无 OAuth 授权，无真实同步 |
| GitHub Actions | CI、手动探测、三次定时框架 | YAML 审计与本地等效链路通过 | 在线结果待分支推送后检查，见 delivery/github-status.json |

总测试：**188 passed**；其中第一阶段 **103 passed**。新增 85 项参数化/单元/故障注入/端到端验证。运行 Python 3.12.14，ruff 通过。GitHub 的 Python 3.10 矩阵尚须在线执行，不能由本地语法/配置推断运行通过。JUnit 和文本日志保存在 [docs/evidence/delivery/](evidence/delivery/)。

## 端到端结果

实际运行：合成列表 → 详情 → 多页企业/岗位 → 标准化 → 匹配 → SQLite → 冲突 → 中文日报/周报 → JSON/CSV → ICS → Google dry-run。

- 5 场合成招聘活动、3 家企业、7 个岗位。
- 4 个 ICS 事件，时间不明确的 669 跳过。
- 同一家综合双选会只生成一个主事件。
- 连续两次运行数量一致，第二次 added/updated/rescheduled/cancelled 全为 0。
- 所有样本明确标为 synthetic，没有包装成学校真实数据。
- 未配置真实采集时产生 skipped 原因，没有学校请求或数据库新增。
- Google dry-run 不读取授权、不调用网络、不写真实映射。

结果样本见 [synthetic-output/](evidence/delivery/synthetic-output/)，独立校验见 [output-validation.json](evidence/delivery/output-validation.json)，原有测试日志见 [original-phase1-tests.txt](evidence/delivery/original-phase1-tests.txt)。

## 审查与提交

功能分支：`feature/recruitment-phases-2-3`。已分功能保存第一阶段基线、第二阶段数据层、匹配/报告/ICS、日历适配器/CLI，以及自动化与文档成果。没有覆盖或合并 main。远端分支/PR 状态另记录 github-status.json，只有实际成功才更新为已推送/已创建。

文件清单见 [file-inventory.json](evidence/delivery/file-inventory.json)。第一阶段证据保留，旧测试只整理导入，没有删除有效断言或跳过测试。原客户端唯一功能扩展为可选记录数上限。

## 当前阻塞与已知限制

1. 学校域名不在当前已生效云端允许列表；不重复尝试已知被拒绝的连接。不能认定学校拒绝匿名访问，也不能确认真实契约。
2. 没有 Google 凭据，未执行 authorize 或真实 sync；Apple 设备显示需要用户实机确认。
3. 日期查询策略独立且默认未验证；不完整结果不删除旧记录、不推断取消。大范围查询可能触及预算，明确失败并保留成功的独立活动。
4. 企业/岗位消失不会自动删除；需要真实源状态及撤回语义才可扩展。旧数据保留，报告标检查时间。
5. Actions cache 可被淘汰；长期完整历史使用私有持久 SQLite/self-hosted runner。公共缓存不含日历身份映射，稳定远端 ID 支持恢复。
6. 文本脱敏是模式筛选，不保证识别任意自由文本个人信息；发布真实招聘数据前人工审核。没有自动数据提交或订阅网站发布。
7. 不推测通勤时间、招聘人数、薪资或缺失结束时间。直接重复导入 ICS 的客户端去重行为不能保证，生产建议使用同一 Google 日历或订阅地址。

## 用户下一步

首先在本地运行 README 的 offline 命令，阅读报告和 ICS；这些动作不需要授权。需要真实学校数据时，在获准网络完成第一阶段探测，核对字段/状态/日期语义，配置映射并主动开启 ENABLE_REAL_COLLECTION。需要真实日历时，按 calendar-setup.md 在私有本地完成 OAuth、检查 dry-run 后显式启用双开关。GitHub Secrets 不要提交到 Git。

后续可基于真实契约加入岗位撤回状态、增量游标、数据质量约束、变更通知、受控 ICS 订阅部署与更完善的匹配语料。当前交付聚焦可靠离线链路和可配置真实适配，不以功能数量替代验证。
