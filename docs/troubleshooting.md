# 故障排查

| 症状 | 证据/含义 | 处理 |
| --- | --- | --- |
| ProxyError / CONNECT 403，http_status=null | 请求尚未到学校 API；不是学校匿名访问拒绝证据 | 保存日志，正常配置允许学校域名，或在获准的本地/校内 runner 运行；不修改代理绕过 |
| 学校 API HTTP 401/403 | 接口实际拒绝当前请求 | 停止并咨询学校的公开访问政策，不登录冒充身份或换 IP |
| HTTP 429 / Retry-After | 源端限流 | 工具有限等待/重试，超过等待限额停止，稍后再运行 |
| 2xx 但 code 不接受 | 业务失败或未验证成功 code | 检查真实文档/JSON，不为消除报错任意接受新 code |
| rows/data 缺失或布局冲突 | 未确认契约或异常 JSON | 保留证据，调整明确字段映射；不得转换为空成功 |
| 重复分页/total 变化/提前空页 | 结果不完整或源在变化 | 本次失败，保留旧数据库，之后重新采集，不推断取消 |
| real 模式 skipped | 默认安全开关关闭，没有实际请求 | 检查 ENABLE_REAL_COLLECTION；只在已验证且获准时启用 |
| real 模式 blocked | 映射未核实或使用 synthetic | 先在可访问环境获得真实响应并核对映射 |
| limited / query unverified | 日期查询覆盖无法确认 | 按策略验证边界和全分页，不能声称已得到未来30天全量 |
| ICS 跳过活动 | 时间缺失、区间无效、状态未确认 | 人工确认真实时间，不补默认两小时或午夜 |
| Google OAuth missing/refresh failed | 未授权、授权过期、范围或网络错误 | 默认 dry-run 可继续；在本地私有位置按授权文档检查 |
| Google ownership conflict | 同 ID 事件不属于本应用 | 停止人工检查，不覆盖他人事件 |
| Google POST 409 或创建后断线 | 稳定 ID 可能已经创建 | 下一次查询该 ID、核对应用所有权后恢复映射，避免重复创建 |
| cache 丢失 | Actions 缓存被淘汰或没有首次状态 | 招聘 ID 仍去重；新增历史基线重建；持久历史改用私有本地/self-hosted SQLite |

真实采集失败时保留 run.json、failure.json 和脱敏请求摘要。第一阶段历史原始证据位于 docs/evidence/cloud-2026-10-14/。公开报告、OAuth 凭据、私人冲突日志分别存放，不能一起打包上传。

调试时不要使用 `env` 或打印 Secret JSON，也不要关闭 TLS 校验。网页字符串只作为数据解析，不执行其中的命令。自动化本地等效验证可以在完全没有学校网络或 Google 凭据时进行。
