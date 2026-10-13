# 第二阶段：采集、数据管理和分析

已实现并通过离线测试；真实学校 JSON 字段与日期语义仍未验证。

## 配置与窗口

`configs/synthetic-schema.json` 仅定义合成契约。`configs/school-schema.example.json` 的映射路径默认全部为 null、`verified=false`，因此不能调用真实采集。验证真实响应后另建映射文件；`verified=true` 表示人工核对字段、开始/结束日期格式、状态枚举，不是自动探测结果。

映射支持点路径，例如 `event.startAt`；未知字段保持 null。开始/结束日期与时间分开时可配置 `{"date_path":"eventDate","time_path":"startClock"}`，两项均缺一不可。数字时间戳必须显式声明 `{"path":"startEpoch","unit":"seconds"}` 或 `milliseconds`，不会猜测单位。无时区的明确钟点按学校所在地 Asia/Shanghai 处理，有偏移的 ISO 时间转换为北京时间。只有日期不会转换为 00:00。无结束时间、不合法区间不生成日历事件。

`status_values` 将核实的源枚举映射为 scheduled/cancelled/postponed/unknown。未知源值保持 unknown；不会自动判定可参会。业务成功 code 可在映射文件的 `success_codes` 中显式配置，默认 200，必须有真实依据。

查询配置：`query.strategy` 可选 baseline（不带日期）、start（传窗口开始日期）、daily（逐日）、monthly（逐月）。`query.verified` 独立表示该策略的范围语义已验证。默认真实查询 unverified，工具只按本地 `[start,end)` 过滤已观察记录，并标注覆盖范围不完整；不能从返回空结果推断窗口无活动或活动取消。

默认未来 30 天；结束日期不包含。`--fair-id` 指定 ID 可回查历史活动，不受当前窗口过滤。指定 ID 不代表发现了窗口中所有活动。未知活动日期保留供人工确认。默认单列表最多 10000 行、1000 页、单次请求预算 200、最多 200 个招聘会、40 个日期查询。超出预算明确失败，不返回伪完整快照。

## SQLite 与历史

`state/offline.db` 和 `state/real.db` 分离 synthetic/school_api 来源。跨来源写入同一 ID 会拒绝，避免把合成记录改成真实记录。

- 招聘会主键：招聘会 ID。
- 企业主键：企业 ID，名称相同不合并；相关招聘会 ID 累积并去重。
- 岗位主键：招聘会 ID + 企业 ID + 岗位 ID。
- `entities` 保存当前标准数据、首次发现、最近检查和单调 revision。
- `history` 保留 added/updated/rescheduled/cancelled 的前后版本与时间。
- `runs` 保存采集完整性、异常和有限的运行审计。
- `calendar_map` 按目标日历 + 招聘会 ID 保存事件映射。

同一标准内容重复采集只更新检查时间，不新增历史版本。一次招聘会的详情、企业和岗位请求全部成功后才将其纳入快照。已经成功获取的独立招聘会可以增量保存；失败不会删除旧活动/岗位。重复 ID 的内容冲突会拒绝整个数据库写入事务，不静默任选一个版本。

只有明确、可信的源取消状态触发取消变更。列表缺失、网络错误、窗口滚动、空结果都不触发取消。源取消记录缺少时间时，可保留该 ID 之前明确观察到的时间用于取消已有事件；这不是新增或推测时间。活动推迟但新时间不明确时不会自动生成日程。

## 岗位匹配

`configs/matching.json` 配置岗位名称模式、要求证据、英文缩写、排除项、权重和阈值。名称命中与要求命中分开解释，要求匹配需有技术角色证据；多个相关技术要求可使通用研发岗获得高分。销售/非研发岗位排除，软件测试不自动视为硬件测试。报告列出分数、优先级和原因。综合双选会按参会岗位推荐，活动标题不参与岗位评分。

## 个人日程与冲突

活动以 `[start,end)` 检测重叠，首尾相接无冲突，支持跨日。缺少钟点/结束时间和已确认取消的活动不参与冲突；不会猜测通勤耗时。默认顺序为面试、笔试、重要专场、综合双选会、普通宣讲，可在 `configs/schedule.json` 调整。

个人记录存于 ignored `private/` 或仓库外私有文件，格式：

```json
{"visibility":"private","activities":[{"id":"my-interview","title":"我的面试","kind":"interview","start":"2026-10-15 10:00","end":"2026-10-15 11:00","location":"自行填写"}]}
```

这个示例不是实际用户记录。用 `--personal-file private/activities.json` 加入私有冲突计算；输出仅写 `private/diagnostics/conflicts.json`，权限 600，不进入招聘数据库、公共报告、ICS 或 Google 同步。算法只给建议，不取消活动。

## 报告与导出

输出 recruitment.json、fairs.csv、companies.csv、positions.csv、daily.md、weekly.md。日报含新增、明天、未来 7 天、高匹配新岗位、改期/取消、冲突与缺失信息；周报含本周新增企业/岗位、排名、推荐活动、过期活动及异常。按数据库首次观察历史计新增，不能由首次运行推断这些记录是学校当天新发布。

导出字段使用标准模型白名单。手机号、邮箱、身份证样式会脱敏，CSV 字符串防公式执行，源码网页内容不执行。此脱敏不保证能识别任意人名或自由文本隐私；公共发布前仍应人工筛选。默认不自动提交任何采集数据。
