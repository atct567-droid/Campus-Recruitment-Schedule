"""Compare documented public request context, without any user login."""

import json
from pathlib import Path

import requests

from campus_recruitment.client import BASE_URL, LIST_PATH, RecruitmentClient
from campus_recruitment.errors import ApiError
from campus_recruitment.models import now_iso


def main():
    output = Path("reports/public-requests")
    output.mkdir(parents=True, exist_ok=True)
    report = {"started_at": now_iso(), "login_performed": False, "comparisons": [], "calendar_writes": False}

    class Session(requests.Session):
        def __init__(self):
            super().__init__()
            self.use_referer = True

        def send(self, prepared, **kwargs):
            if self.use_referer:
                prepared.headers["Referer"] = BASE_URL + "/jobfairSearch"
            response = super().send(prepared, **kwargs)
            entry = {"url": prepared.url, "http_status": response.status_code,
                     "referer_sent": self.use_referer, "guest_cookies_sent": "Cookie" in prepared.headers,
                     "authorization_sent": "Authorization" in prepared.headers,
                     "cookie_names_received": [cookie.name for cookie in response.cookies]}
            try:
                entry["response_json"] = response.json()
            except ValueError:
                entry["response_format"] = "not JSON"
            report["comparisons"].append(entry)
            return response

    session = Session()
    client = RecruitmentClient(session=session, interval=2, request_budget=40, retries=0)
    try:
        try:
            client.get_json(LIST_PATH, {"pageNum": 1, "pageSize": 10})
        except ApiError:
            pass
        try:
            collection = client.list_fairs("2026-10-14", page_size=50)
            report["dated_collection"] = collection.as_dict()
        except ApiError as error:
            report["dated_error"] = error.as_dict()
        try:
            detail = client.fair_detail(666)
            report["sample_detail"] = detail
            report["sample_companies"] = client.companies(666, page_size=100).as_dict()
            report["sample_positions_page1"] = client.get_json("/api/view/jobFair/positionList", {"jobFairId": 666, "pageNum": 1, "pageSize": 50})
        except ApiError as error:
            report["sample_error"] = error.as_dict()
    finally:
        client.close()
        report["requests_attempted"] = client.attempts
        report["finished_at"] = now_iso()
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("BEGIN_PUBLIC_REQUESTS_VERIFICATION_JSON")
        preview = {**report, "comparisons": [
            {**entry, "response_json": {**entry["response_json"], "rows": entry["response_json"]["rows"][:2]}}
            if isinstance(entry.get("response_json"), dict) and "rows" in entry["response_json"] else entry
            for entry in report["comparisons"]]}
        if "dated_collection" in preview:
            preview["dated_collection"] = {**preview["dated_collection"], "rows": preview["dated_collection"]["rows"][:5]}
        if "sample_companies" in preview:
            preview["sample_companies"] = {**preview["sample_companies"], "rows": preview["sample_companies"]["rows"][:3]}
        if "sample_positions_page1" in preview:
            page = preview["sample_positions_page1"]
            if isinstance(page.get("rows"), list):
                preview["sample_positions_page1"] = {**page, "rows": page["rows"][:3]}
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print("END_PUBLIC_REQUESTS_VERIFICATION_JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
