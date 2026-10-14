"""Resume other public fairs while retaining the explicit large-fair page limit."""

import csv
import json
import os
from pathlib import Path

from campus_recruitment.client import COMPANY_PATH, POSITION_PATH, RecruitmentClient
from campus_recruitment.errors import ApiError
from campus_recruitment.matcher import JobMatcher
from campus_recruitment.models import Snapshot, now_iso
from campus_recruitment.normalizer import Normalizer, clean_text
from campus_recruitment.parsing import parse_page
from campus_recruitment.reports import generate_reports, public_data
from campus_recruitment.storage import Store
from scripts import collect_public_preview as preview

POSITION_KEYS = ("positionId", "companyId", "companyName", "positionName", "positionDesc", "personsNum",
                 "salaryDesc", "needDegreeDesc", "workPlace")
COMPANY_KEYS = ("companyId", "companyName", "companyTypeDesc", "companyTradeDesc", "companyPlaceDesc")


class BoundedPublicClient(RecruitmentClient):
    def __init__(self, **options):
        super().__init__(max_pages=5, **options)
        self.responses = {}

    def get_json(self, path, params=None):
        payload = super().get_json(path, params)
        if path in (COMPANY_PATH, POSITION_PATH):
            self.responses.setdefault(path, []).append(payload)
        return payload

    def visible(self, path, identifier):
        try:
            self.fetch_all(path, params={"jobFairId": identifier}, page_size=10)
            complete = True
        except ApiError as error:
            if error.kind != "pagination" or str(error) != "Page limit reached; result is incomplete":
                raise
            complete = False
        pages = self.responses[path]
        return [row for payload in pages for row in parse_page(payload).rows], complete, parse_page(pages[-1])


def main():
    output = preview.OUTPUT
    if os.environ.get("ENABLE_REAL_COLLECTION") != "true":
        preview.save(output / "disabled.json", {"status": "skipped", "reason": "Explicit real collection opt-in is missing"})
        return 2
    listing = json.loads((output / "listing.json").read_text())
    old = {name: json.loads((output / (name + ".json")).read_text()) for name in
           ("public-records", "completeness", "recruitment")}
    if not listing.get("listing_complete") or old["public-records"].get("provenance") != "school_api":
        raise ValueError("Resume requires an actual verified school listing and public snapshot")
    completed = {item["jobFairId"] for item in old["completeness"]["per_fair"]}
    remaining = [row for row in listing["fairs"] if str(row["jobFairId"]) not in completed and row["jobFairId"] != 666]
    # Continue only the other already discovered public events; do not repeat successful collection.
    preview.save(output / "listing.json", {**listing, "fairs": remaining})
    schema = Normalizer.from_file(preview.ROOT / "configs/school-public-schema.json")
    code = preview.details(schema)
    new = {name: json.loads((output / (name + ".json")).read_text()) for name in
           ("public-records", "completeness", "recruitment")}
    preview.save(output / "listing.json", listing)
    companies = {(row["jobFairId"], str(row["companyId"])): row for row in old["public-records"]["companies"] + new["public-records"]["companies"]}
    positions = {(row["jobFairId"], str(row["companyId"]), str(row["positionId"])): row for row in old["public-records"]["positions"] + new["public-records"]["positions"]}
    errors = new["completeness"]["errors"]
    big_metadata = {}
    events = []
    client = BoundedPublicClient(interval=2, request_budget=20, emit=events.append,
                                 public_referer=schema.config["public_referer"])

    def add_company(row, source):
        if row.get("companyId") is None:
            return
        key = ("666", str(row["companyId"]))
        record = {key: clean_text(row.get(key)) if isinstance(row.get(key), str) else row.get(key) for key in COMPANY_KEYS}
        record.update(jobFairId="666", provenance="school_api", fair_subcollection_complete=False, source_endpoint=source)
        companies[key] = record

    def add_position(row, source, parent=None):
        row = {**(parent or {}), **row}
        if row.get("positionId") is None or row.get("companyId") is None:
            return
        key = ("666", str(row["companyId"]), str(row["positionId"]))
        record = {key: clean_text(row.get(key)) if isinstance(row.get(key), str) else row.get(key) for key in POSITION_KEYS}
        record.update(jobFairId="666", provenance="school_api", fair_subcollection_complete=False, source_endpoint=source)
        positions[key] = record
        if ("666", str(row["companyId"])) not in companies:
            add_company(row, source + ".company")

    try:
        detail = client.fair_detail(666)
        for row in detail.get("jobFairCompanyCOList") or []:
            add_company(row, "getJobFair.jobFairCompanyCOList")
            for child in row.get("jobFairPositionList") or []:
                add_position(child, "getJobFair.jobFairCompanyCOList.jobFairPositionList", {"companyId": row["companyId"], "companyName": row.get("companyName")})
        for row in detail.get("jobFairPositionCOList") or []:
            add_position(row, "getJobFair.jobFairPositionCOList")
        group, group_complete, group_page = client.visible(COMPANY_PATH, "666")
        for row in group:
            add_company(row, "companyGroupList")
            for child in row.get("positionList") or []:
                add_position(child, "companyGroupList.positionList", {"companyId": row["companyId"], "companyName": row.get("companyName")})
        jobs, jobs_complete, job_page = client.visible(POSITION_PATH, "666")
        for row in jobs:
            add_position(row, "positionList")
        big_metadata = {"jobFairId": "666", "subcollection_complete": group_complete and jobs_complete,
                        "company_endpoint_total": group_page.total, "position_endpoint_total": job_page.total,
                        "company_pages_read": len(client.responses[COMPANY_PATH]), "position_pages_read": len(client.responses[POSITION_PATH]),
                        "company_rows_read": len(group), "position_rows_read": len(jobs),
                        "readable_positions_also_parsed_from": "already returned public detail/group position lists",
                        "public_page_limit": 5, "page_6_or_later_requested": False}
    except ApiError as error:
        errors.append(error.as_dict())
    finally:
        client.close()
    matcher = JobMatcher.from_file(preview.ROOT / "configs/matching.json")
    for row in positions.values():
        position = schema.position(row, row["jobFairId"])
        matcher.match(position)
        row.update(matching_score=position.matching_score, matching_priority=position.matching_priority,
                   matching_reasons=position.matching_reasons)
    metadata = old["completeness"]["per_fair"] + new["completeness"]["per_fair"] + ([big_metadata] if big_metadata else [])
    full_ids = completed | {item["jobFairId"] for item in new["completeness"]["per_fair"]}
    result = {"generated_at": now_iso(), "start": listing["start"], "end_exclusive": listing["end_exclusive"],
              "timezone": "Asia/Shanghai", "provenance": "school_api", "login_performed": False,
              "real_calendar_sync_performed": False, "collection_complete": False,
              "public_page_limit_for_large_fair": 5, "fairs": listing["fairs"],
              "companies": list(companies.values()), "positions": list(positions.values()), "errors": errors,
              "warnings": ["综合双选会第6页在前次匿名采集中HTTP400；本次不请求受限页，仅展示已验证公开前5页及响应内直接提供的岗位，不能声称911等声明数量全部采集。"]}
    preview.save(output / "public-records.json", result)
    completeness = {"generated_at": now_iso(), "expected_fairs": len(listing["fairs"]), "fully_collected_fairs": len(full_ids),
                    "large_fair_partial": True, "unique_companies": len({row["companyId"] for row in companies.values()}),
                    "company_fair_relations": len(companies), "position_fair_relations": len(positions),
                    "collection_complete": False, "public_scope_complete": not errors and len(full_ids) == 45 and bool(big_metadata),
                    "new_requests": new["completeness"]["requests_attempted"] + client.attempts,
                    "per_fair": metadata, "errors": errors, "warnings": result["warnings"]}
    preview.save(output / "completeness.json", completeness)
    preview.save(output / "large-fair-requests.json", events)
    for name, rows in (("companies", list(companies.values())), ("positions", list(positions.values()))):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with (output / (name + "-with-details.csv")).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in public_data(rows):
                values = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()}
                writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for key, value in values.items()})
    snapshot = Snapshot(mode="real", query_verified=True, checked_at=now_iso(), complete=False, warnings=result["warnings"], errors=errors)
    snapshot.fairs = [schema.fair(row, listing["finished_at"]) for row in listing["fairs"]]
    snapshot.companies = [schema.company(row, row["jobFairId"]) for row in companies.values()]
    snapshot.positions = [schema.position(row, row["jobFairId"]) for row in positions.values()]
    for position in snapshot.positions:
        matcher.match(position)
    with Store(Path("work/public-observed.db")) as store:
        store.apply(snapshot)
        generate_reports(store, output, as_of=preview.START, snapshot=snapshot)
    print(json.dumps(completeness, ensure_ascii=False))
    return code if code else (0 if completeness["public_scope_complete"] else 2)


if __name__ == "__main__":
    raise SystemExit(main())
