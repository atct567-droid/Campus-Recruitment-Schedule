"""Public ICS subscriptions: observed times, durable IDs, no account credentials."""

import hashlib
import json
from datetime import date, datetime, timedelta

from .client import COMPANY_PATH, POSITION_PATH
from .errors import ApiError
from .ical import eligible, export_ics, summary
from .models import SHANGHAI, now_iso
from .parsing import parse_page
from .reports import public_data


def normalize_snapshot(records, normalizer, *, listing=None):
    """Convert reviewed school response fields; never promote fixtures to real data."""
    if normalizer.synthetic or not normalizer.verified or records.get("provenance") != "school_api":
        raise ValueError("Subscription requires verified actual school data")
    coverage = listing or records
    if coverage.get("listing_complete") is not True or coverage.get("date_semantics_verified_for_run") is not True:
        raise ValueError("A complete, verified date listing is required; keep the previous feed")
    if records.get("errors"):
        raise ValueError("Snapshot errors require review before publication")
    checked_at = records["generated_at"]
    if datetime.fromisoformat(checked_at).tzinfo is None:
        raise ValueError("Snapshot timestamp must include timezone")
    companies, positions = {}, {}
    incomplete = set(records.get("incomplete_fair_ids", []))
    for row in records["companies"]:
        if row.get("private") or row.get("provenance") != "school_api":
            raise ValueError("Private or synthetic company data is forbidden")
        fair_id = str(row["jobFairId"])
        company = normalizer.company(row, fair_id, checked_at).as_dict()
        key = company["company_id"]
        if key in companies:
            company["related_job_fair_ids"] = sorted(set(companies[key]["related_job_fair_ids"] + [fair_id]))
        companies[key] = company
        if row.get("fair_subcollection_complete") is False:
            incomplete.add(fair_id)
    for row in records["positions"]:
        if row.get("private") or row.get("provenance") != "school_api":
            raise ValueError("Private or synthetic position data is forbidden")
        fair_id = str(row["jobFairId"])
        position = normalizer.position(row, fair_id, checked_at).as_dict()
        position["matching_score"] = row.get("matching_score", 0)
        position["matching_priority"] = row.get("matching_priority", "low")
        position["matching_reasons"] = row.get("matching_reasons", [])
        positions[(fair_id, position["company_id"], position["position_id"])] = position
        if row.get("fair_subcollection_complete") is False:
            incomplete.add(fair_id)
    fairs = {}
    for row in records["fairs"]:
        if row.get("private") or row.get("provenance", "school_api") != "school_api":
            raise ValueError("Private or synthetic activities are forbidden")
        fair = normalizer.fair(row, checked_at).as_dict()
        identifier = fair["job_fair_id"]
        fair["calendar_notes"] = ["活动时间来自学校公开接口，请以学校最新公告为准。"]
        if not fair["status_verified"]:
            fair["calendar_notes"].append("取消状态尚未核实，日历标为暂定，不依据列表缺失推断取消。")
        if identifier in incomplete:
            fair["calendar_notes"].append("企业与岗位明细为已取得的公开部分，未收齐；不代表全部参会企业和岗位。")
        if identifier in fairs and fairs[identifier] != fair:
            raise ValueError("Conflicting duplicate fair ID")
        fairs[identifier] = fair
    return public_data({"schema_version": 1, "provenance": "school_api", "generated_at": checked_at,
                        "listing_complete": True, "query_semantics_verified": True,
                        "collection_complete": records.get("collection_complete") is True,
                        "incomplete_fair_ids": sorted(incomplete), "fairs": list(fairs.values()),
                        "companies": list(companies.values()), "positions": list(positions.values())})


def public_pages(client, path, fair_id, *, limit=5):
    """Read at most five public pages; stop on denial or inconsistent pagination."""
    rows, seen, expected = [], set(), None
    try:
        for number in range(1, limit + 1):
            page = parse_page(client.get_json(path, {"jobFairId": fair_id, "pageNum": number, "pageSize": 10}))
            metadata = page.total, page.total_page
            if expected is not None and metadata != expected:
                raise ApiError("pagination", "Pagination metadata changed")
            expected = metadata
            for row in page.rows:
                signature = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if signature in seen:
                    raise ApiError("pagination", "Repeated row in public pages")
                seen.add(signature)
                rows.append(row)
            if not page.rows:
                advertised_empty = page.total_page and number <= page.total_page and not (number == 1 and page.total in (None, 0) and page.total_page == 1)
                if page.total not in (None, len(rows)) or advertised_empty:
                    raise ApiError("pagination", "Empty page before declared end")
                return rows, True, None
            end_total = page.total is not None and len(rows) == page.total
            end_pages = page.total_page is not None and number == page.total_page
            if end_total or end_pages:
                if (page.total is not None and not end_total) or (page.total_page is not None and not end_pages):
                    raise ApiError("pagination", "Public pagination totals disagree")
                return rows, True, None
            if page.total is not None and len(rows) > page.total:
                raise ApiError("pagination", "Public rows exceed declared total")
    except ApiError as error:
        return rows, False, error.as_dict()
    return rows, False, {"kind": "public_page_limit", "message": "Stopped before page 6; public data is partial"}


def collect_public_snapshot(client, normalizer, matcher, *, start, days=30):
    """Complete the date listing first; subcollection limits cannot remove fairs."""
    query = normalizer.config.get("query", {})
    if normalizer.synthetic or not normalizer.verified or query.get("verified") is not True or query.get("observed_semantics") != "day_equals":
        raise ValueError("Reviewed actual daily-query mapping is required")
    if not 1 <= days <= 30:
        raise ValueError("Subscription window must be 1..30 days")
    fairs = {}
    for number in range(days):
        day = start + timedelta(days=number)
        for row in client.list_fairs(day.isoformat(), page_size=10).rows:
            fair = normalizer.fair(row)
            if fair.start_date != day.isoformat():
                raise ApiError("query_semantics", "Daily query returned a different or missing date")
            identifier = fair.job_fair_id
            if identifier in fairs and fairs[identifier] != row:
                raise ApiError("pagination", "Conflicting fair in daily listing")
            fairs[identifier] = row
            if len(fairs) > 200:
                raise ApiError("record_limit", "Subscription fair limit exceeded")
    raw_companies, raw_positions, incomplete, diagnostics = {}, {}, [], []

    def company(row, fair_id):
        normalized = normalizer.company(row, fair_id)
        raw_companies[(fair_id, normalized.company_id)] = {
            "jobFairId": fair_id, "companyId": normalized.company_id,
            "companyName": normalized.company_name, "provenance": "school_api"}

    def position(row, fair_id, parent=None):
        row = {**(parent or {}), **row}
        normalized = normalizer.position(row, fair_id)
        matcher.match(normalized)
        raw_positions[(fair_id, normalized.company_id, normalized.position_id)] = {
            "jobFairId": fair_id, "companyId": normalized.company_id, "positionId": normalized.position_id,
            "positionName": normalized.position_name, "positionDesc": normalized.requirements,
            "personsNum": normalized.recruitment_count, "matching_score": normalized.matching_score,
            "matching_priority": normalized.matching_priority, "matching_reasons": normalized.matching_reasons,
            "provenance": "school_api"}
        if (fair_id, normalized.company_id) not in raw_companies:
            company({"companyId": normalized.company_id, "companyName": row.get("companyName")}, fair_id)

    for fair_id in fairs:
        complete = True
        try:
            detail = client.fair_detail(fair_id)
            if normalizer.identifier(detail, "fair", "job_fair_id") != fair_id:
                raise ApiError("normalization", "Returned fair detail ID differs")
            fairs[fair_id] = detail
            for row in detail.get("jobFairCompanyCOList") or []:
                company(row, fair_id)
                for child in row.get("jobFairPositionList") or []:
                    position(child, fair_id, row)
            for row in detail.get("jobFairPositionCOList") or []:
                position(row, fair_id)
        except ApiError as error:
            complete = False
            diagnostics.append({"jobFairId": fair_id, **error.as_dict()})
        for path in (COMPANY_PATH, POSITION_PATH):
            rows, finished, error = public_pages(client, path, fair_id)
            complete = complete and finished
            if error:
                diagnostics.append({"jobFairId": fair_id, "path": path, **error})
            for row in rows:
                if path == COMPANY_PATH:
                    company(row, fair_id)
                    for child in row.get("positionList") or []:
                        position(child, fair_id, row)
                else:
                    position(row, fair_id)
        if not complete:
            incomplete.append(fair_id)
    for row in list(raw_companies.values()) + list(raw_positions.values()):
        row["fair_subcollection_complete"] = row["jobFairId"] not in incomplete
    records = {"provenance": "school_api", "generated_at": now_iso(), "listing_complete": True,
               "date_semantics_verified_for_run": True, "collection_complete": not incomplete,
               "incomplete_fair_ids": incomplete, "fairs": list(fairs.values()),
               "companies": list(raw_companies.values()), "positions": list(raw_positions.values()), "errors": []}
    return normalize_snapshot(records, normalizer), diagnostics


def build_feed(snapshot, previous=None, *, published_at=None):
    """Merge changed observed events; absence is never evidence of cancellation."""
    if (snapshot.get("provenance") != "school_api" or snapshot.get("listing_complete") is not True
            or snapshot.get("query_semantics_verified") is not True):
        raise ValueError("Refuse synthetic/unverified/incomplete date listings")
    previous = previous or {"schema_version": 1, "events": {}}
    if previous.get("schema_version") != 1:
        raise ValueError("Unknown subscription state schema")
    for old in previous["events"].values():
        if old["fair"].get("private") or old["fair"].get("provenance") != "school_api":
            raise ValueError("Unsafe previous subscription state")
    stamp = datetime.fromisoformat(snapshot["generated_at"])
    if stamp.tzinfo is None:
        raise ValueError("Snapshot timestamp must include timezone")
    publication = datetime.fromisoformat(published_at) if published_at else stamp
    if publication.tzinfo is None or publication < stamp:
        raise ValueError("Publication timestamp must include timezone and not predate the source")
    for row in snapshot["companies"] + snapshot["positions"]:
        if row.get("private") or row.get("provenance") != "school_api":
            raise ValueError("Unsafe company or position in subscription")
    state, skipped, current_ids = {}, [], set()
    for original in snapshot["fairs"]:
        fair = dict(original)
        identifier = fair["job_fair_id"]
        if identifier in current_ids:
            raise ValueError("Duplicate subscription fair ID")
        current_ids.add(identifier)
        if fair.get("private") or fair.get("provenance") != "school_api":
            raise ValueError("Subscription contains private or non-school activity")
        if not eligible(fair, allow_observed=True):
            skipped.append({"job_fair_id": identifier, "reason": "时间不明确或状态不适用；未创建新事件"})
            if identifier in previous["events"]:
                state[identifier] = previous["events"][identifier]
            continue
        description = summary(fair, snapshot["positions"], snapshot["companies"], include_all_positions=True)
        record = {key: fair.get(key) for key in ("job_fair_id", "title", "start_time", "end_time", "location",
                                                "source_url", "status", "status_verified", "provenance", "calendar_notes")}
        record["calendar_description"] = description
        digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = previous["events"].get(identifier)
        same = old and old["hash"] == digest
        state[identifier] = {"fair": record, "hash": digest,
                             "revision": old["revision"] if same else (old["revision"] + 1 if old else 0),
                             "updated_at": old["updated_at"] if same else publication.isoformat(timespec="seconds")}
    cutoff = (stamp.astimezone(SHANGHAI) - timedelta(days=90)).date()
    for identifier, old in previous["events"].items():
        if identifier in state:
            continue
        if old["fair"].get("private") or old["fair"].get("provenance") != "school_api":
            raise ValueError("Unsafe previous subscription state")
        if date.fromisoformat(old["fair"]["end_time"][:10]) >= cutoff:
            state[identifier] = old
    fairs = []
    for item in state.values():
        fair = dict(item["fair"], revision=item["revision"], calendar_updated_at=item["updated_at"])
        fairs.append(fair)
    # Descriptions are cached with the semantic content, preserving absent events' company/job context.
    calendar, unexpected_skips = export_ics(sorted(fairs, key=lambda fair: (fair["start_time"], fair["job_fair_id"])),
                                           stamp=stamp, reminder_minutes=None, reminder_offsets=(1440, 30),
                                           allow_observed=True, calendar_name="桂航校招", include_all_positions=True)
    if unexpected_skips:
        raise ValueError("Stored subscription events failed ICS eligibility")
    metadata = {"generated_at": snapshot["generated_at"], "published_at": publication.isoformat(timespec="seconds"),
                "provenance": "school_api", "timezone": "Asia/Shanghai",
                "events": len(state), "observed_fairs": len(snapshot["fairs"]), "skipped": skipped,
                "collection_complete": snapshot.get("collection_complete") is True,
                "incomplete_fair_ids": snapshot.get("incomplete_fair_ids", []),
                "retained_unobserved_events": sorted(set(state) - current_ids),
                "status_note": "取消状态未核实的活动标为 TENTATIVE；列表缺失不推断取消。",
                "real_account_calendar_write": False}
    return calendar, {"schema_version": 1, "events": state}, metadata
