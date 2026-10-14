# 已验证的学校公开招聘访问

2026-10-14 已由全新、未登录的浏览器和 requests 实际取得招聘 JSON。四个已知接口在 `pageSize=10` 下均为 HTTP 200、业务 code 200；没有发送 Authorization、用户 Cookie 或登录凭据。

早期请求使用 pageSize=50/100，服务器返回 HTTP 400 和“请登录后再浏览”。这不足以判断所有招聘信息都要登录。实际匿名 pageSize=10 的请求即使没有 Referer 也成功；Referer 仅作为已验证的公开页面上下文选项。证据运行：[四接口匿名请求](https://github.com/atct567-droid/Campus-Recruitment-Schedule/actions/runs/38509364071)、[未登录浏览器观察](https://github.com/atct567-droid/Campus-Recruitment-Schedule/actions/runs/38508522058)。

## 日期与字段

当前观察中 calendarStartDate 筛选**当天**，不是“从该日开始”。实际开始字段为 startTime，结束字段为 endTime。未来 30 天必须逐日查询再按 jobFairId 去重。

2026-10-14 至 2026-11-12（结束边界 2026-11-13 不包含）逐日、全分页取得 **46 场**活动；同时检查 10-01、10-13 和 11-13 边界。本次唯一一致假设为 day_equals，34 次请求的证据来自[日期与活动目录运行](https://github.com/atct567-droid/Campus-Recruitment-Schedule/actions/runs/38510083248)。不带日期的列表 total=545 包含历史数据，不能当作未来30天的活动数。

字段配置见 configs/school-public-schema.json：招聘会 jobFairId/jobFairName/startTime/endTime/jobFairAddress；企业 companyId/companyName；岗位 positionId/companyId/positionName/positionDesc/personsNum。工资、学历、工作地点等公开字段另保留在明细 CSV/JSON 中。

state=2 的实际说明是“审核通过”，尚不能用审核状态判断活动取消。当前取消映射保持未知，不自动创建或取消真实日历。

## 公开分页的范围

本次综合双选会 jobFairId=666 的企业分页第1至5页成功，第6页 HTTP 400。因此后续预览保守地停在已验证公开前5页，不请求更深页，不伪造账号、Token、Cookie 或通过重排查询规避限制。其他已发现的小场次继续读取正常公开的全部页。

同时解析公开 getJobFair 与 companyGroupList 响应中直接返回的岗位列表，按 `(jobFairId, companyId, positionId)` 去重并保留 source_endpoint。不会据此声称已读取受限页。

结果明确区分：活动目录完整、某场企业/岗位子集完整、整体 collection_complete，以及公共预览任务是否完成。此次实际公开预览取得112家去重企业、408个去重岗位、433条参会关联，其中综合双选会217条；45场子明细已收齐，整体 collection_complete=false。综合双选会仍有未取得的企业/岗位；页面宣传数量与接口 total 也不同、随时间更新。以每场 completeness.json 为准，不能将“公开预览运行成功”当作“声明数量全部采集”。

全部名单：[Markdown](evidence/school-public-2026-10-14/full-report.md)、[活动CSV](evidence/school-public-2026-10-14/fairs.csv)、[企业CSV](evidence/school-public-2026-10-14/companies.csv)、[岗位CSV](evidence/school-public-2026-10-14/positions.csv)、[JSON](evidence/school-public-2026-10-14/recruitment.json)、[独立核对](evidence/school-public-2026-10-14/validation.json)。

## 复现

在允许访问学校网站的环境中：

```bash
python -m pip install -e '.[test]'
campus-recruitment --anchor-date 2026-10-14 --page-size 10 --date-field startTime --public-page jobfairSearch --output-dir reports/phase1-public
ENABLE_REAL_COLLECTION=true campus-manager run --mode real --schema configs/school-public-schema.json --start 2026-10-14 --days 30 --db state/real.db --output-dir reports/real
```

完整验证/严格采集在遇到受限后续页时会非零退出，标为不完整。真实模式配置使用每页10条、420次总请求预算、默认两秒间隔；显式 --request-budget 覆盖预算。ENABLE_REAL_COLLECTION 仍需显式设置，仓库定时开关未自动启用。

公开子集预览由 scripts/collect_public_preview.py 和 scripts/resume_public_preview.py 保存已观察数据及限制，恢复时复用成功快照，避免重复请求。记录保留 school_api 来源，和 synthetic 测试样本分开。联系方式脱敏；服务账号、日历凭据从未用于招聘采集。
