# Apple 日历订阅：无需 Google Cloud

用户选择 ICS 后，本项目生成学校真实公开活动的订阅文件，发布到独立 `ics-feed` 分支。固定 HTTPS 地址：

```text
https://raw.githubusercontent.com/atct567-droid/Campus-Recruitment-Schedule/ics-feed/recruitment.ics
```

无需 Google 服务账号、Google Calendar ID、Apple 密码或任何账户密钥。这个地址公开提供学校招聘活动，不包含个人日程。初始数据来自北京时间 2026-10-14 12:34:02 的已验证快照：46 场活动，其中 10 月 15 日有 9 场；综合双选会企业/岗位明细为部分，描述已标注。所有活动开始和结束时间都来自接口，未推测时间。

## 手机官网链接修正

2026-10-14 用户反馈点击事件链接进入404。已用 Chromium 的 iPhone 视口及 User-Agent 模拟复现：学校 `/jobfairSearch` 和 `/index` 返回HTTP200，但手机端实际跳转到 `/m/pages/public/404`。这说明仅检查HTTP状态不能确认页面有效。

当前事件改为对应活动的直接移动详情页，例如铭普光磁：

```text
https://bysjyw.guat.edu.cn/m/pages/jobfair/jobfair-detail?jobFairId=675
```

已验证675、709、666三场的直接移动详情页，在桌面与iPhone模拟浏览器中均HTTP200，显示对应活动标题、时间、地点及正文，没有进入404。桌面 `/jobfairDetail/675` 未附query时虽然200但内容为空，因此不使用这类未完整加载的地址。真实Apple设备上的点击仍需用户核对，模拟浏览器不冒充真实设备验证。

同一订阅地址的46场活动已经修订官网链接，并将地址单独一行显示。原UID和时间保持一致，SEQUENCE由0增为1，LAST-MODIFIED更新到实际发布时间。`metadata.json` 的generated_at仍为招聘数据采集时间，published_at为文件发布时间，不把修复链接说成重新采集。设备尚显示旧地址时，可在日历App的日历列表尝试下拉刷新，或等待系统刷新；刷新节奏由Apple客户端决定。

路由证据：[移动404复现](https://github.com/atct567-droid/Campus-Recruitment-Schedule/actions/runs/38546130186)、[详情页验证](https://github.com/atct567-droid/Campus-Recruitment-Schedule/actions/runs/38546684564)，持久记录见 `docs/evidence/apple-ics-links-2026-10-14/`。

## 添加订阅

**iPhone/iPad：**打开“日历”App → 底部“日历”列表 → “添加日历” → “添加订阅日历”，粘贴上面的完整 HTTPS 地址，预览后选择“订阅”。系统版本不同可从设置中的日历账户找到“添加订阅日历”。可选订阅账户以设备实际显示为准。将日历命名为“桂航校招”。

**Mac：**打开“日历”App → “文件” → “新建日历订阅”，粘贴同一地址，点击“订阅”。可选择 iCloud 位置以便在同一 Apple 账号的其他设备显示，自动刷新建议每小时。客户端实际刷新时间由 Apple 决定，文件中声明的6小时刷新周期只是提示。

这是外部订阅日历，活动内容由订阅文件维护，设备中以查看为主。文件内有提前1天与30分钟的 DISPLAY 提醒；设备可能忽略订阅提醒，添加订阅时核对提醒选项并检查实际通知。成功生成/读取 ICS 不等于已经在用户设备订阅成功。

## 事件与数据规则

- 每个 jobFairId 只生成一个主事件；企业与岗位进入说明，匹配分数高的岗位优先，最多20条，并包含地点与官网链接。
- UID 由 jobFairId 稳定生成，内容改变时增加 SEQUENCE 和 LAST-MODIFIED；同一快照重复生成文件保持不变。
- 取消状态映射还未验证，因此初始学校活动使用 `STATUS:TENTATIVE`，含“暂定”说明，不伪称已经确认。只有源明确且已验证的取消状态才输出 `CANCELLED`。
- 缺失、失败或部分响应不推断取消。日期目录失败/语义变化时不发布，保留上一份可用订阅；单场企业/岗位受限时仍展示时间明确的主事件，并注明部分明细。
- 时间不完整、不合法的新增活动跳过；原有活动保留最后一次可靠记录，缺失与跳过项见 metadata.json。历史活动保留90天，不根据从未来窗口消失就取消。
- 只解析已验证公开字段，限每页10条、每个明细接口最多前5页、至少2秒请求间隔、总预算420次，不访问深分页或绕过限制。
- 真实订阅拒绝 synthetic/unverified/private 数据；公开描述经过电话、邮箱和证件信息脱敏。订阅分支只存公开 ICS、公开状态和日志，不存 Google 或 Apple 凭据。

## 自动更新

`.github/workflows/ics-subscription.yml` 在北京时间06:00、12:00、18:00采集未来30天，生成并独立验证，然后更新 `ics-feed` 分支。`GITHUB_TOKEN` 仅需 `contents: write`，不需要任何新增 Secret；GitHub 的 cron 可能延迟。工作流 checkout 使用独立公开分支、固定并发锁，发布失败保留旧版本。

**GitHub 只会执行默认分支上的定时工作流。**当前工作流在功能分支，PR 保持 Draft，尚未合并 main，所以发布初始订阅文件并不意味着自动更新已启用。将经过验证的工作流合并到默认分支后才能启用 cron；首次定时真实采集成功仍需检查 Actions 日志及订阅 metadata.json 的采集时间，不能以离线测试代替。

## 本地复现与验证

```bash
python -m pip install -e '.[test]'
python scripts/build_ics_feed.py \
  --snapshot docs/evidence/school-public-2026-10-14/recruitment.json \
  --listing docs/evidence/school-public-2026-10-14/listing.json \
  --output work/ics-feed
python scripts/validate_ics_feed.py work/ics-feed
```

获准网络中的真实更新：

```bash
ENABLE_REAL_COLLECTION=true python scripts/build_ics_feed.py --collect --output work/ics-feed
python scripts/validate_ics_feed.py work/ics-feed
```

当前云环境的学校访问仍受网络策略限制；上述实时命令用于正常可达学校接口的 GitHub Actions runner 或用户本地设备，不绕过当前代理限制。独立验证使用 `icalendar` 解析真实文件，对时间、UID、事件数量、说明、SEQUENCE、UTF-8折行及两条提醒逐项核对。
