"""Chinese reports and allowlisted public exports; personal schedule stays separate."""
import csv
import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .models import SHANGHAI, Company, Fair, Position
from .schedule import conflicts, daily_order, fair_activities


def redact_public(text):
    if not isinstance(text, str):
        return text
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[联系电话已隐藏]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", text)
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[证件信息已隐藏]", text)
    return text


def public_data(value):
    if isinstance(value, dict):
        return {key: public_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return redact_public(value)


def _date_of(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date() if "T" in value else datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _md(value):
    text = str(value if value is not None else "未确认")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def _fair_table(fairs):
    lines = ["| ID | 活动 | 开始 | 结束 | 地点 | 状态 | 来源 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for fair in fairs:
        lines.append("| " + " | ".join(_md(fair.get(k)) for k in
                                      ("job_fair_id", "title", "start_time", "end_time", "location", "status", "source_url")) + " |")
    if not fairs:
        lines.append("| — | 当前已观察数据中无记录 | — | — | — | — | — |")
    return lines


def _position_table(positions):
    lines = ["| 招聘会 | 企业ID | 岗位 | 分数 | 优先级 | 匹配原因 | 来源 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for position in positions:
        values = [position[k] for k in ("job_fair_id", "company_id", "position_name", "matching_score", "matching_priority")]
        values += ["；".join(position["matching_reasons"]), position["source_url"]]
        lines.append("| " + " | ".join(_md(value) for value in values) + " |")
    if not positions:
        lines.append("| — | — | 无符合条件记录 | — | — | — | — |")
    return lines


def generate_reports(store, output_dir, *, as_of, snapshot=None):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fairs, companies, positions = (store.list(kind) for kind in ("fair", "company", "position"))
    ranked = sorted(positions, key=lambda p: (-p["matching_score"], p["job_fair_id"], p["position_id"]))
    public_fairs = store.models("fair")
    overlaps = conflicts(fair_activities(public_fairs))
    history = store.history()
    runs = store.runs()
    # Serialize only fields declared in public dataclasses plus public update metadata.
    classes = {"fairs": Fair, "companies": Company, "positions": Position}
    data = {"schema_version": 1, "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "as_of": as_of.isoformat(), "timezone": "Asia/Shanghai",
            "provenance": sorted({f["provenance"] for f in fairs}),
            "collection_complete": snapshot.complete if snapshot else None,
            "query_semantics_verified": snapshot.query_verified if snapshot else None,
            "warnings": snapshot.warnings if snapshot else [],
            "errors": snapshot.errors if snapshot else [], "conflicts": overlaps}
    for name, values in (("fairs", fairs), ("companies", companies), ("positions", ranked)):
        allowlist = set(classes[name].__dataclass_fields__) | {"first_seen_at", "revision"}
        data[name] = [{key: value for key, value in row.items() if key in allowlist} for row in values]
    data = public_data(data)
    (output / "recruitment.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in ("fairs", "companies", "positions"):
        # Include a stable header even for empty exports.
        headers = list(classes[name].__dataclass_fields__) + ["first_seen_at", "revision"]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        for row in data[name]:
            flattened = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
            # Prevent spreadsheet formula injection in public strings.
            for key, value in flattened.items():
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    flattened[key] = "'" + value
            writer.writerow(flattened)
        (output / f"{name}.csv").write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")

    tomorrow = as_of + timedelta(days=1)
    future_end = as_of + timedelta(days=7)
    active = [f for f in fairs if f["status"] != "cancelled"]
    upcoming = [f for f in active if (day := _date_of(f["start_date"])) is not None and as_of <= day < future_end]
    high = [p for p in ranked if p["matching_priority"] == "high"]
    today_history = [h for h in history if _date_of(h["checked_at"]) == as_of]
    week_start = as_of - timedelta(days=as_of.weekday())
    week_end = week_start + timedelta(days=7)
    week_history = [h for h in history if (day := _date_of(h["checked_at"])) is not None and week_start <= day < week_end]
    new_fair_ids = {h["entity_key"] for h in today_history if h["kind"] == "fair" and h["change_type"] == "added"}
    new_high_keys = {h["entity_key"] for h in today_history if h["kind"] == "position" and h["change_type"] == "added"}
    common = ["所有日期为 Asia/Shanghai；缺失时间不推测。", "",
              "数据来源类型：" + ("、".join(data["provenance"]) or "无数据"), "",
              "synthetic 为合成测试样本，不代表学校真实招聘数据。", "",
              "更新于：" + data["generated_at"], "",
              f"本次采集完整性：{data['collection_complete']}；日期语义验证：{data['query_semantics_verified']}", ""]
    daily = [f"# 每日招聘报告 {as_of.isoformat()}", "", *common,
             "## 当天新增招聘会", "", *_fair_table([f for f in fairs if f["job_fair_id"] in new_fair_ids]), "",
             "## 明天即将举行", "", *_fair_table([f for f in active if _date_of(f["start_date"]) == tomorrow]), "",
             "## 未来 7 天活动", "", *_fair_table(upcoming), "",
             "## 新增高匹配岗位", "", *_position_table([p for p in high if store.key("position", p) in new_high_keys]), "",
             "## 活动改期与取消", ""]
    daily += [f"- {h['entity_key']}：{h['change_type']}；原时间 {_md(h['previous'].get('start_time') if h['previous'] else None)} → {_md(h['current'].get('start_time'))}" for h in today_history if h["change_type"] in {"rescheduled", "cancelled"}] or ["无已确认变更。"]
    daily += ["", "## 时间冲突与同日顺序", ""]
    daily += [f"- {_md(c['left_title'])} / {_md(c['right_title'])}：{c['overlap_start']} 至 {c['overlap_end']}；建议优先 ID {c['suggested_priority_id']}，不自动取消。" for c in overlaps] or ["无已确认冲突；未知时间未参与判断。"]
    daily += [f"- 顺序：{_md(a.title)} / {_md(a.start)} / {_md(a.location)}" for a in daily_order(fair_activities(public_fairs))]
    daily += ["", "## 需要人工确认", ""]
    daily += [f"- {f['job_fair_id']}：{_md('；'.join(f['issues']))}" for f in fairs if f["issues"]] or ["当前已观察样本无额外缺失项。"]
    daily += [f"- {_md(w)}" for w in data["warnings"]]

    new_companies = {h["entity_key"] for h in week_history if h["kind"] == "company" and h["change_type"] == "added"}
    new_positions = {h["entity_key"] for h in week_history if h["kind"] == "position" and h["change_type"] == "added"}
    weekly = [f"# 每周招聘报告 {week_start.isoformat()} 至 {(week_end - timedelta(days=1)).isoformat()}", "", *common,
              "## 本周新增企业", ""]
    weekly += [f"- {c['company_id']}：{_md(c['company_name'])}" for c in companies if c["company_id"] in new_companies] or ["无已观察新增企业。"]
    weekly += ["", "## 本周新增岗位", "", *_position_table([p for p in ranked if store.key("position", p) in new_positions]), "",
               "## 高匹配岗位排行榜", "", *_position_table(high), "",
               "## 未来 7 天推荐活动", "", *_fair_table([f for f in upcoming if any(p["job_fair_id"] == f["job_fair_id"] for p in high)]), "",
               "## 已过期活动", "", *_fair_table([f for f in fairs if (day := _date_of(f["start_date"])) is not None and day < as_of]), "",
               "## 数据采集异常摘要", ""]
    exceptions = [r for r in runs if r["metadata"]["errors"] or r["metadata"]["warnings"]]
    weekly += [f"- {r['checked_at']} / {r['mode']}：{_md(json.dumps(r['metadata']['errors'] + r['metadata']['warnings'], ensure_ascii=False))}" for r in exceptions] or ["最近运行没有记录异常；不代表已验证真实接口。"]
    for name, lines in (("daily.md", daily), ("weekly.md", weekly)):
        (output / name).write_text(public_data("\n".join(lines)) + "\n", encoding="utf-8")
    return data
