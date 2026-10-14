"""A repeatable phase-1 probe; never writes calendars or claims collection success."""
import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import RecruitmentClient
from .errors import ApiError

SHANGHAI = ZoneInfo("Asia/Shanghai")


def today_shanghai() -> date:
    return datetime.now(SHANGHAI).date()


def date_argument(value: str) -> date:
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Date must be YYYY-MM-DD") from None


def _observed_date(value):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(SHANGHAI).date() if result.tzinfo else result.date()
    except ValueError:
        return None


def date_evidence(queries, *, id_field, date_field):
    result = {"status": "unverified", "date_field": date_field,
              "reason": "Specify the actual fair-start date field; identical/empty results do not establish semantics.",
              "observations": [], "consistent_hypotheses": []}
    for query in queries:
        result["observations"].append({"calendarStartDate": query["calendarStartDate"],
                                       "row_count": len(query["collection"]["rows"]),
                                       "total": query["collection"]["total"],
                                       "totalPage": query["collection"]["totalPage"]})
    dated = [q for q in queries if q["calendarStartDate"] is not None]
    if not date_field or len(dated) < 3:
        return result
    universe = {}
    observed = []
    for query in queries:
        ids = set()
        for row in query["collection"]["rows"]:
            identifier = row.get(id_field)
            day = _observed_date(row.get(date_field))
            if identifier is None or isinstance(identifier, (bool, dict, list)) or day is None:
                result["reason"] = "A row lacks a usable ID/start date; date semantics remain unverified."
                return result
            key = str(identifier)
            if key in ids or key in universe and universe[key] != day:
                result["reason"] = "Duplicate IDs or changing dates make comparisons inconclusive."
                return result
            universe[key] = day
            ids.add(key)
        if query["calendarStartDate"] is not None:
            observed.append((date.fromisoformat(query["calendarStartDate"]), ids))
    if not universe or len({frozenset(ids) for _, ids in observed}) < 2:
        result["reason"] = "Empty or identical results cannot distinguish date-query hypotheses."
        return result
    models = {
        "day_equals": lambda day, q: day == q,
        "start_date_inclusive": lambda day, q: day >= q,
        "start_date_exclusive": lambda day, q: day > q,
        "same_month": lambda day, q: (day.year, day.month) == (q.year, q.month),
        "parameter_ignored": lambda day, q: True,
    }
    consistent = [name for name, predicate in models.items()
                  if all(ids == {key for key, day in universe.items() if predicate(day, query_day)}
                         for query_day, ids in observed)]
    result["consistent_hypotheses"] = consistent
    result["reason"] = "Hypotheses are comparisons with observed rows, not a guaranteed API contract."
    if len(consistent) == 1:
        result["status"] = "observed_evidence"
        result["reason"] = "One hypothesis matches this run; verify boundary coverage before phase 2."
    return result


def run_probe(client, *, anchor: date, page_size=50, fair_id=None,
              id_field="jobFairId", date_field=None):
    report = {"phase": 1, "started_at": datetime.now(SHANGHAI).isoformat(),
              "base_url": client.base_url, "anonymous": True,
              "scope": "Anonymous API contract verification only; no calendar synchronization",
              "anchor_date": anchor.isoformat(), "timezone": "Asia/Shanghai",
              "accepted_business_codes": sorted(client.success_codes),
              "settings": {"page_size": page_size, "interval_seconds": client.interval,
                           "connect_timeout": client.timeout[0], "read_timeout": client.timeout[1],
                           "retries": client.retries, "request_budget": client.request_budget,
                           "max_pages": client.max_pages},
              "endpoints": {}, "queries": [], "failures": []}

    def execute(name, action):
        try:
            value = action()
            report["endpoints"][name] = {"status": "verified", "anonymous_json": True}
            return value
        except ApiError as exc:
            report["endpoints"][name] = {"status": "failed", "error": exc.as_dict()}
            report["failures"].append({"operation": name, **exc.as_dict()})
            return None

    baseline = execute("jobFair/list", lambda: client.list_fairs(page_size=page_size))
    if baseline is None:
        for name in ("getJobFair", "jobFair/positionList", "position/companyGroupList"):
            report["endpoints"][name] = {"status": "not_tested", "reason": "List failed; no discovered fair ID"}
        # No further probing after a list failure or environment/policy denial.
        report["date_semantics"] = {"status": "unverified", "reason": "Initial list request failed"}
    else:
        report["queries"].append({"calendarStartDate": None, "collection": baseline.as_dict()})
        query_dates = {anchor - timedelta(days=1), anchor, anchor + timedelta(days=1),
                       anchor + timedelta(days=30), anchor.replace(day=1)}
        # An explicitly identified start-date field permits useful boundary tests.
        if date_field:
            for row in baseline.rows:
                observed = _observed_date(row.get(date_field))
                if observed is not None:
                    query_dates.update({observed - timedelta(days=1), observed, observed + timedelta(days=1)})
                    break
        for query_day in sorted(query_dates):
            try:
                collection = client.list_fairs(query_day.isoformat(), page_size=page_size)
                report["queries"].append({"calendarStartDate": query_day.isoformat(), "collection": collection.as_dict()})
            except ApiError as exc:
                report["failures"].append({"operation": "calendarStartDate", "query_date": query_day.isoformat(), **exc.as_dict()})
                break
        report["date_semantics"] = date_evidence(report["queries"], id_field=id_field, date_field=date_field)
        if any(f["operation"] == "calendarStartDate" for f in report["failures"]):
            report["date_semantics"].update(status="unverified", reason="A date query failed; comparisons are incomplete")

        chosen = fair_id
        if chosen is None:
            for query in report["queries"]:
                for row in query["collection"]["rows"]:
                    if row.get(id_field) is not None:
                        chosen = row[id_field]
                        break
                if chosen is not None:
                    break
        fatal = any(f["kind"] in {"proxy_policy", "access_denied", "redirect", "retry_after", "request_budget"}
                    or f["http_status"] == 429 for f in report["failures"])
        if chosen is None or fatal:
            reason = "No usable fair ID; empty lists do not verify detail APIs" if chosen is None else "Stopped after access/rate/budget limit"
            for name in ("getJobFair", "jobFair/positionList", "position/companyGroupList"):
                report["endpoints"][name] = {"status": "not_tested", "reason": reason}
        else:
            try:
                chosen = client._fair_id(chosen)
            except ValueError as exc:
                report["failures"].append({"operation": "fair_id", "kind": "schema", "message": str(exc), "http_status": None})
                for name in ("getJobFair", "jobFair/positionList", "position/companyGroupList"):
                    report["endpoints"][name] = {"status": "not_tested", "reason": "Invalid discovered fair ID"}
            else:
                report["sample_jobFairId"] = chosen
                tasks = [("getJobFair", lambda: client.fair_detail(chosen)),
                         ("jobFair/positionList", lambda: client.positions(chosen, page_size=page_size)),
                         ("position/companyGroupList", lambda: client.companies(chosen, page_size=page_size))]
                stopped = False
                for name, action in tasks:
                    if stopped:
                        report["endpoints"][name] = {"status": "not_tested", "reason": "Stopped after access/rate/budget limit"}
                        continue
                    value = execute(name, action)
                    if value is not None:
                        report["endpoints"][name]["sample"] = value.as_dict() if hasattr(value, "as_dict") else value
                    else:
                        last = report["failures"][-1]
                        stopped = last["kind"] in {"proxy_policy", "access_denied", "redirect", "retry_after", "request_budget"} or last["http_status"] == 429
    report["api_validation"] = "verified" if all(e["status"] == "verified" for e in report["endpoints"].values()) else "incomplete"
    report["phase1_complete"] = report["api_validation"] == "verified" and report["date_semantics"]["status"] == "observed_evidence" and not report["failures"]
    report["requests_attempted"] = client.attempts
    report["finished_at"] = datetime.now(SHANGHAI).isoformat()
    return report


def render_markdown(report):
    lines = ["# 桂林航天工业学院接口验证报告（第一阶段）", "",
             f"- 验证时间：{report['started_at']}（Asia/Shanghai）",
             f"- 地址：{report['base_url']}",
             f"- 接口验证：{report['api_validation']}",
             f"- 第一阶段完成：{report['phase1_complete']}",
             f"- 实际请求次数：{report['requests_attempted']}",
             "- 使用匿名 GET；不发送登录凭据，不创建日历事件。", "",
             "| 接口 | 状态 | 说明 |", "| --- | --- | --- |"]
    for name, outcome in report["endpoints"].items():
        description = outcome.get("reason") or outcome.get("error", {}).get("message", "HTTP、业务 code、结构和分页均通过当前样本验证")
        description = description.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {name} | {outcome['status']} | {description} |")
    evidence = report["date_semantics"]
    lines.extend(["", "## 日期查询语义", "", f"状态：{evidence['status']}", "", evidence["reason"],
                  "", "未获得足够有效响应时，不推断 calendarStartDate 是当天、起始日期或月份筛选。",
                  "observed_evidence 只表示本次样本支持一个假设；第二阶段前仍需复核边界和覆盖范围。"])
    if evidence.get("consistent_hypotheses"):
        lines.extend(["", "本次一致假设：" + ", ".join(evidence["consistent_hypotheses"])])
    if report["failures"]:
        lines.extend(["", "## 失败记录", "", "```json", json.dumps(report["failures"], ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "详细请求证据见 requests.jsonl；分页与原始业务样本见 verification.json。",
                  "报告失败不代表学校服务故障，也不代表接口需要登录。", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify GUAT public APIs anonymously; no calendar writes")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/phase1"))
    parser.add_argument("--anchor-date", type=date_argument, default=today_shanghai())
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--read-timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--request-budget", type=int, default=200)
    parser.add_argument("--success-code", action="append", help="Explicit accepted code; default is 200")
    parser.add_argument("--job-fair-id", help="Real known fair ID, otherwise use a returned row")
    parser.add_argument("--id-field", default="jobFairId")
    parser.add_argument("--date-field", help="Actual start-date field after inspecting a real response")
    parser.add_argument("--public-page", choices=["index", "jobfairSearch", "positionSearch"],
                        help="Same-origin public page Referer observed from an anonymous browser")
    args = parser.parse_args(argv)
    if not 1 <= args.page_size <= 100:
        parser.error("--page-size must be between 1 and 100")
    try:
        if args.job_fair_id is not None:
            RecruitmentClient._fair_id(args.job_fair_id)
        client = RecruitmentClient(success_codes=args.success_code or (200,), interval=args.interval,
                                   timeout=(args.connect_timeout, args.read_timeout), retries=args.retries,
                                   max_pages=args.max_pages, request_budget=args.request_budget,
                                   public_referer=("https://bysjyw.guat.edu.cn/" + args.public_page) if args.public_page else None)
    except ValueError as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "requests.jsonl").open("w", encoding="utf-8") as log:
        def emit(entry):
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log.flush()
        client.emit = emit
        try:
            report = run_probe(client, anchor=args.anchor_date, page_size=args.page_size,
                               fair_id=args.job_fair_id, id_field=args.id_field, date_field=args.date_field)
        finally:
            client.close()
    (args.output_dir / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "verification.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"API validation: {report['api_validation']}; date semantics: {report['date_semantics']['status']}; requests: {report['requests_attempted']}")
    print(f"Evidence: {args.output_dir}")
    return 0 if report["phase1_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
