# 桂林航天工业学院接口验证报告（第一阶段）

- 验证时间：2026-10-14T01:22:50.715686+08:00（Asia/Shanghai）
- 地址：https://bysjyw.guat.edu.cn
- 接口验证：incomplete
- 第一阶段完成：False
- 实际请求次数：1
- 使用匿名 GET；不发送登录凭据，不创建日历事件。

| 接口 | 状态 | 说明 |
| --- | --- | --- |
| jobFair/list | failed | HTTPSConnectionPool(host='bysjyw.guat.edu.cn', port=443): Max retries exceeded with url: /api/view/jobFair/list?pageNum=1&pageSize=50 (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |
| getJobFair | not_tested | List failed; no discovered fair ID |
| jobFair/positionList | not_tested | List failed; no discovered fair ID |
| position/companyGroupList | not_tested | List failed; no discovered fair ID |

## 日期查询语义

状态：unverified

Initial list request failed

未获得足够有效响应时，不推断 calendarStartDate 是当天、起始日期或月份筛选。
observed_evidence 只表示本次样本支持一个假设；第二阶段前仍需复核边界和覆盖范围。

## 失败记录

```json
[
  {
    "operation": "jobFair/list",
    "kind": "proxy_policy",
    "message": "HTTPSConnectionPool(host='bysjyw.guat.edu.cn', port=443): Max retries exceeded with url: /api/view/jobFair/list?pageNum=1&pageSize=50 (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden')))",
    "http_status": null
  }
]
```

详细请求证据见 requests.jsonl；分页与原始业务样本见 verification.json。
报告失败不代表学校服务故障，也不代表接口需要登录。
