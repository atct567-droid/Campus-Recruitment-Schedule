# 用户授权后的真实匿名采集验证

此页保留11:38、pageSize=50的历史失败。随后 pageSize=10 已实际匿名成功，并取得46场、112家企业、408个去重岗位的公开数据；部分深分页仍受限。最新状态见 [公开访问说明](../../school-public-access.md) 和 [全部真实公开名单](../school-public-2026-10-14/full-report.md)。不能用这个历史失败判断所有招聘信息都要登录。

验证时间：2026-10-14 11:38（Asia/Shanghai）。独立 GitHub Actions runner，顺序请求、两秒间隔、TLS 校验开启，不发送账号、Token 或 Cookie，不涉及日历写入。

运行记录：[38507244349](https://github.com/atct567-droid/Campus-Recruitment-Schedule/actions/runs/38507244349)。完整证据 artifact：`guat-live-preview-38507244349`。

| 请求 | HTTP | 实际结果 |
| --- | --- | --- |
| `/api/view/jobFair/list?calendarStartDate=2026-10-14&pageNum=1&pageSize=50` | 400 | JSON 明确要求登录 |
| `/index` | 200 | HTML 应用壳可访问，未从其中提取招聘记录 |

招聘会接口实际响应：

```json
{"code":400,"msg":"请登录后再浏览","data":null}
```

真实招聘会 0 场、企业 0 家、岗位 0 条；尚未取得任何招聘数据。无可用 fair ID，因此详情、企业、岗位接口未测试。日期筛选语义、字段映射和未来 30 天的完整性仍未验证。

此前云环境的 CONNECT 403 发生在代理层；本次独立 runner 已得到学校 URL 的 HTTP/JSON 响应，当前阻塞是匿名列表请求被要求登录。这不表示所有入口都必须登录，也不表示使用正常本人账号一定采集成功。

停止继续请求受限接口，没有尝试伪造 Token、猜测 ID 或规避登录。已有 synthetic 测试数据继续与真实结果区分。

后续可在本人正常登录且学校允许自动读取的本地环境中验证访问；登录凭据不得粘贴到聊天、公开仓库或日志。也可在本人正常访问后导出相关 JSON 供离线解析、去重与完整性核对。当前工具的匿名模式不会自动使用浏览器 Cookie。
