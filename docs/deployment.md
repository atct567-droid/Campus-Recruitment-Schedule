# 安装、部署与启用

## 本地离线运行

Python 3.10+；Windows 将 .venv/bin 替换为 .venv/Scripts。以下在仓库根目录运行。

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test,calendar]'
.venv/bin/ruff check src tests scripts
.venv/bin/python -m pytest -q
.venv/bin/campus-manager run --mode offline
.venv/bin/python scripts/validate_outputs.py reports/offline
```

无需学校网络和 Google 凭据。默认使用明确标为 synthetic 的 fixtures/synthetic-recruitment.json，参考日期 2026-10-14；生成 5 场活动、3 家企业、7 个岗位、4 个 ICS 事件。缺少时间的一场活动跳过。重复运行数据库保持这些数量，不重复新增。报告读取 reports/offline/，数据库是 ignored state/offline.db。

```bash
.venv/bin/campus-manager run --mode offline --start 2026-10-14 --end 2026-11-13 --db state/offline.db --output-dir reports/offline
.venv/bin/campus-manager run --mode offline --fair-id 671 --db state/history-demo.db --output-dir reports/history-demo
.venv/bin/campus-manager sync --db state/offline.db --output-dir reports/calendar-plan
```

最后一个命令仅 dry-run，不需要安装 calendar extra 或配置凭据。`--no-reports` 关闭招聘报告；`--no-calendar-dry-run` 关闭同步计划；`--no-ics` 关闭 ICS。历史示例 671 也是合成 ID，不是学校验证记录。

## 获准网络上的真实采集

先在正常可访问学校网站的机器执行第一阶段探测，用返回 JSON 核对路径、业务 code、字段与日期语义；不要根据样例 ID 666 推断字段。

复制 configs/school-schema.example.json 为自己核实的映射文件，配置字段/状态/code；将 verified 改为 true 只表示确实已人工核对。query.verified 必须有范围验证依据，否则保留 false，程序给 limited 结果及退出码 2。

```bash
export ENABLE_REAL_COLLECTION=true
.venv/bin/campus-manager run --mode real --schema configs/school-schema.verified.json --db state/real.db --days 30 --output-dir reports/real
```

该命令只是启用步骤，本环境没有执行真实采集。开始/结束日期的本地窗口是半开区间，不假设 calendarStartDate 自带 30 天范围。不完整快照不会判定缺失活动取消。

## GitHub Actions

在功能分支审查并合并后，定时任务才会按默认分支的工作流生效。默认真实采集关闭，只写 skip 原因。Actions 页面手动运行 Recruitment scheduled pipeline，选择 offline，先检查 artifact 中的报告/ICS。

需要真实采集时设置 Repository Variables：

| 名称 | 用途 |
| --- | --- |
| ENABLE_REAL_COLLECTION | 仅字符串 true 启用真实采集 |
| GUAT_SCHEMA_PATH | 已验证且入库的公开字段映射配置路径 |
| ENABLE_REAL_CALENDAR_SYNC | 仅字符串 true 允许手动真实同步步骤 |

Google 服务账号 JSON 使用 GOOGLE_CREDENTIALS Repository Secret，日历 ID 使用 GOOGLE_CALENDAR_ID Repository Variable，具体见 google-calendar-service-account.md。不要把真实凭据当工作流输入或仓库文件。工作流输出仅 reports/，状态缓存保留日历 ID 哈希隔离的事件映射并脱敏，个人日程参数没有开放给公共工作流。定时实际写入另需 ENABLE_SCHEDULED_CALENDAR_SYNC=true 和 ENABLE_REAL_CALENDAR_SYNC=true，由用户确认后开启。

Actions cache 并非永久存储；需要长期历史，请使用校内/本地 runner 的持久数据库，再用 SQLite backup 做一致性备份。原始数据库不应上传到公共 artifact。

## self-hosted runner

在学校允许访问的可信 Linux 主机注册 runner，添加 guat-network 标签。将手动探测及定时工作流的 runs-on 改为 `[self-hosted, linux, x64, guat-network]`，只运行可信仓库代码，不向外部 PR 暴露宿主机或凭据。

跨 runner 的脱敏缓存已经保留哈希事件映射；缓存被淘汰仍可从稳定 Google ID 恢复。需要长期可靠持久化时，将 self-hosted runner 的专用私有状态目录作为 `--db`，不要被 checkout 清理；设置访问权限 700/600，并定期用 Store.backup 备份。公开缓存不保存明文日历身份、凭据或其他日程。

只有学校明确允许的网络可以运行真实采集。收到学校 401/403/429、登录跳转或云代理拒绝时遵守限制，不换 IP 或关闭代理/TLS 绕过。

## 退出码

0：请求范围成功（离线是合成测试）、dry-run 完成，或真实采集关闭后安全跳过。2：未验证契约被阻止、日期覆盖 limited、采集/配置/OAuth/同步失败。看 run.json/failure.json，而不是仅看退出码；skip 不等于实际采集成功。
