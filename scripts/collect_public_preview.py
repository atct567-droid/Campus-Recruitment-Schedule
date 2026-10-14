"""User-authorized, bounded preview of the school's public recruitment APIs."""

import argparse
import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

from campus_recruitment.client import COMPANY_PATH, LIST_PATH, POSITION_PATH, RecruitmentClient
from campus_recruitment.errors import ApiError
from campus_recruitment.matcher import JobMatcher
from campus_recruitment.models import Snapshot, now_iso
from campus_recruitment.normalizer import Normalizer, clean_text
from campus_recruitment.parsing import parse_page
from campus_recruitment.probe import date_evidence
from campus_recruitment.reports import generate_reports, public_data
from campus_recruitment.storage import Store

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("reports/live-public")
START = date(2026, 10, 14)
END = START + timedelta(days=30)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(public_data(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def listing(normalizer):
    report = {"started_at": now_iso(), "start": START.isoformat(), "end_exclusive": END.isoformat(),
              "timezone": "Asia/Shanghai", "login_performed": False, "calendar_writes": False,
              "baseline_scope": "First page for comparison; historical total is not the 30-day event count",
              "queries": [], "fairs": [], "errors": [], "listing_complete": False}
    events = []
    client = RecruitmentClient(interval=2, request_budget=120, max_pages=120, emit=events.append,
                               public_referer=normalizer.config.get("public_referer"))
    try:
        baseline = parse_page(client.get_json(LIST_PATH, {"pageNum": 1, "pageSize": 10}))
        report["baseline_total"] = baseline.total
        report["queries"].append({"calendarStartDate": None, "collection": {
            "rows": baseline.rows, "total": baseline.total, "totalPage": baseline.total_page,
            "pages_fetched": 1, "scope": "first_page_only"}})
        days = {START + timedelta(days=n) for n in range(30)} | {START - timedelta(days=1), END, START.replace(day=1)}
        by_id = {}
        for day in sorted(days):
            result = client.list_fairs(day.isoformat(), page_size=10)
            report["queries"].append({"calendarStartDate": day.isoformat(), "collection": result.as_dict()})
            if START <= day < END:
                for row in result.rows:
                    identifier = normalizer.identifier(row, "fair", "job_fair_id")
                    if identifier in by_id and by_id[identifier] != row:
                        raise ApiError("pagination", "Fair data changed between date queries")
                    by_id[identifier] = row
            print(json.dumps({"date": day.isoformat(), "fairs": len(result.rows), "pages": result.pages_fetched}), flush=True)
        report["fairs"] = sorted(by_id.values(), key=lambda row: (row.get("startTime") or "", row["jobFairId"]))
        evidence = date_evidence(report["queries"], id_field="jobFairId", date_field="startTime")
        report["date_semantics"] = evidence
        report["date_semantics_verified_for_run"] = evidence["status"] == "observed_evidence" and evidence["consistent_hypotheses"] == ["day_equals"]
        report["listing_complete"] = report["date_semantics_verified_for_run"]
    except ApiError as error:
        report["errors"].append(error.as_dict())
    finally:
        client.close()
        report["requests_attempted"] = client.attempts
        report["finished_at"] = now_iso()
        save(OUTPUT / "listing.json", report)
        save(OUTPUT / "listing-requests.json", events)
    print(json.dumps({"stage": "listing", "fairs": len(report["fairs"]), "listing_complete": report["listing_complete"],
                      "date_semantics": report.get("date_semantics"), "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["listing_complete"] else 2


def details(normalizer):
    indexed = json.loads((OUTPUT / "listing.json").read_text(encoding="utf-8"))
    if not indexed.get("listing_complete"):
        raise ValueError("Public daily-query coverage has not been verified")
    events = []
    observed = {"companies": {}, "positions": {}}
    metadata = []
    matcher = JobMatcher.from_file(ROOT / "configs/matching.json")
    snapshot = Snapshot(mode="real", query_verified=True, checked_at=now_iso())

    class CapturingClient(RecruitmentClient):
        def get_json(self, path, params=None):
            value = super().get_json(path, params)
            if path in (COMPANY_PATH, POSITION_PATH):
                fair_id = str(params["jobFairId"])
                kind = "companies" if path == COMPANY_PATH else "positions"
                keys = ("companyId", "companyName", "companyTypeDesc", "companyTradeDesc", "companyPlaceDesc") if kind == "companies" else (
                    "positionId", "jobfairPositionId", "companyId", "companyName", "positionName", "positionDesc", "personsNum",
                    "salaryDesc", "needDegreeDesc", "workPlace", "stateDesc")
                for row in parse_page(value).rows:
                    public = {key: clean_text(row.get(key)) if isinstance(row.get(key), str) else row.get(key) for key in keys}
                    public["jobFairId"] = fair_id
                    public["provenance"] = "school_api"
                    key = (fair_id, str(row["companyId"])) if kind == "companies" else (fair_id, str(row["companyId"]), str(row["positionId"]))
                    observed[kind][key] = public
            return value

    client = CapturingClient(interval=2, request_budget=420, max_pages=150, max_records=20000, emit=events.append,
                             public_referer=normalizer.config.get("public_referer"))
    try:
        for summary in indexed["fairs"]:
            identifier = str(summary["jobFairId"])
            detail = client.fair_detail(identifier)
            if normalizer.identifier(detail, "fair", "job_fair_id") != identifier:
                raise ApiError("normalization", "Returned detail ID differs from the requested ID")
            fair = normalizer.fair(detail, snapshot.checked_at)
            if fair.start_date is None or not START <= date.fromisoformat(fair.start_date) < END:
                snapshot.warnings.append("活动 " + identifier + " 时间改变或未知，需要人工复核")
                continue
            companies = client.companies(identifier, page_size=10)
            for attempt in range(2):
                try:
                    positions = client.positions(identifier, page_size=10)
                    break
                except ApiError as error:
                    if error.kind != "pagination" or attempt == 1:
                        raise
                    print(json.dumps({"fair": identifier, "retry": "One bounded retry after pagination changed"}), flush=True)
            snapshot.fairs.append(fair)
            snapshot.companies.extend(normalizer.company(row, identifier, snapshot.checked_at) for row in companies.rows)
            snapshot.positions.extend(normalizer.position(row, identifier, snapshot.checked_at) for row in positions.rows)
            meta = {"jobFairId": identifier, "advertised_companies": summary.get("companyCount"),
                    "advertised_positions": summary.get("positionCount"), "company_endpoint_total": companies.total,
                    "position_endpoint_total": positions.total, "companies_fetched": len(companies.rows),
                    "positions_fetched": len(positions.rows), "company_pages": companies.pages_fetched,
                    "position_pages": positions.pages_fetched}
            metadata.append(meta)
            if meta["advertised_companies"] != companies.total or meta["advertised_positions"] != positions.total:
                snapshot.warnings.append("活动 " + identifier + " 的页面宣传数量与分页接口总量不同；以此次实际返回数据为准，差异见 completeness.json")
            print(json.dumps({"fair": identifier, "companies": len(companies.rows), "positions": len(positions.rows),
                              "requests": client.attempts}, ensure_ascii=False), flush=True)
        snapshot.complete = len(snapshot.fairs) == len(indexed["fairs"]) and not snapshot.errors
    except ApiError as error:
        snapshot.errors.append(error.as_dict())
    finally:
        client.close()
    for position in snapshot.positions:
        matcher.match(position)
    with Store(Path("work/live-public.db")) as store:
        store.apply(snapshot)
        generate_reports(store, OUTPUT, as_of=START, snapshot=snapshot)
    # Preserve every actually observed public record, including clearly flagged partial pages.
    complete_ids = {fair.job_fair_id for fair in snapshot.fairs}
    company_keys = {(fair_id, company.company_id) for company in snapshot.companies for fair_id in company.related_job_fair_ids}
    position_keys = {(position.job_fair_id, position.company_id, position.position_id) for position in snapshot.positions}
    for kind, records in observed.items():
        for record in records.values():
            record["fair_subcollection_complete"] = record["jobFairId"] in complete_ids
            if kind == "positions":
                position = normalizer.position(record, record["jobFairId"], snapshot.checked_at)
                matcher.match(position)
                record["matching_score"] = position.matching_score
                record["matching_priority"] = position.matching_priority
                record["matching_reasons"] = position.matching_reasons
    public = {"generated_at": now_iso(), "start": START.isoformat(), "end_exclusive": END.isoformat(),
              "timezone": "Asia/Shanghai", "provenance": "school_api", "login_performed": False,
              "real_calendar_sync_performed": False, "collection_complete": snapshot.complete,
              "fairs": indexed["fairs"],
              "companies": [row for key, row in observed["companies"].items() if row["jobFairId"] not in complete_ids or key in company_keys],
              "positions": [row for key, row in observed["positions"].items() if row["jobFairId"] not in complete_ids or key in position_keys],
              "errors": snapshot.errors, "warnings": snapshot.warnings}
    save(OUTPUT / "public-records.json", public)
    for kind in ("companies", "positions"):
        rows = public_data(public[kind])
        columns = list(rows[0]) if rows else ["jobFairId", "companyId"]
        with (OUTPUT / (kind + "-with-details.csv")).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                values = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()}
                values = {key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for key, value in values.items()}
                writer.writerow(values)
    completeness = {"started_at": snapshot.checked_at, "finished_at": now_iso(), "page_size": 10,
                    "authorization_or_user_cookies_sent": False, "interval_seconds": 2,
                    "requests_attempted": client.attempts, "collection_complete": snapshot.complete,
                    "expected_fairs": len(indexed["fairs"]), "fully_collected_fairs": len(snapshot.fairs),
                    "unique_companies": len({row["companyId"] for row in public["companies"]}),
                    "company_fair_relations": len(public["companies"]), "position_fair_relations": len(public["positions"]),
                    "per_fair": metadata, "errors": snapshot.errors, "warnings": snapshot.warnings}
    save(OUTPUT / "completeness.json", completeness)
    save(OUTPUT / "detail-requests.json", events)
    print(json.dumps(completeness, ensure_ascii=False))
    return 0 if snapshot.complete else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["listing", "details"], required=True)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ENABLE_REAL_COLLECTION") != "true":
        save(OUTPUT / "disabled.json", {"status": "skipped", "reason": "Explicit real collection opt-in is missing"})
        return 2
    normalizer = Normalizer.from_file(ROOT / "configs/school-public-schema.json")
    if normalizer.synthetic or not normalizer.verified:
        raise ValueError("Verified public field mapping is required")
    return listing(normalizer) if args.stage == "listing" else details(normalizer)


if __name__ == "__main__":
    raise SystemExit(main())
