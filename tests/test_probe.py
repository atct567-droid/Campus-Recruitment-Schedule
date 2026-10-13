import json
from datetime import date

import pytest
import requests
from conftest import response

from campus_recruitment.probe import date_evidence, main, render_markdown, run_probe


def page(rows):
    return response({"code": 200, "rows": rows, "total": len(rows), "totalPage": 1 if rows else 0})


def test_blocked_list_is_not_anonymous_success(make_client):
    client, session, _, _ = make_client([requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden")])
    report = run_probe(client, anchor=date(2026, 10, 14))
    assert report["api_validation"] == "incomplete" and report["phase1_complete"] is False
    assert report["date_semantics"]["status"] == "unverified"
    assert report["endpoints"]["getJobFair"]["status"] == "not_tested"
    assert len(session.sent) == 1
    assert "proxy_policy" in render_markdown(report)


def test_successful_empty_lists_do_not_verify_detail_or_dates(make_client):
    client, _, _, _ = make_client([page([])] * 6)
    report = run_probe(client, anchor=date(2026, 10, 14), date_field="startDate")
    assert report["endpoints"]["jobFair/list"]["status"] == "verified"
    assert report["endpoints"]["getJobFair"]["status"] == "not_tested"
    assert report["date_semantics"]["status"] == "unverified"
    assert report["phase1_complete"] is False


def test_real_id_used_and_all_endpoint_samples_recorded(make_client):
    fair = {"jobFairId": "abc", "startDate": "2026-10-20"}
    client, _, _, _ = make_client([page([fair])] * 6 + [response({"code": 200, "data": fair}), page([]), page([])])
    report = run_probe(client, anchor=date(2026, 10, 14))
    assert report["sample_jobFairId"] == "abc" and report["api_validation"] == "verified"
    assert report["phase1_complete"] is False
    assert report["endpoints"]["getJobFair"]["sample"] == fair
    assert report["endpoints"]["jobFair/positionList"]["sample"]["total"] == 0


def test_rate_limit_stops_other_endpoints(make_client):
    fair = {"jobFairId": "abc"}
    client, session, _, _ = make_client([page([fair]), response(status=429)], retries=0)
    report = run_probe(client, anchor=date(2026, 10, 14))
    assert report["endpoints"]["getJobFair"]["status"] == "not_tested"
    assert len(session.sent) == 2 and report["phase1_complete"] is False


def test_detail_denial_stops_subsequent_endpoints(make_client):
    client, session, _, _ = make_client([page([{"jobFairId": 1}])] * 6 + [response(status=403)])
    report = run_probe(client, anchor=date(2026, 10, 14))
    assert report["endpoints"]["jobFair/positionList"]["status"] == "not_tested"
    assert len(session.sent) == 7


def query(day, rows):
    return {"calendarStartDate": day, "collection": {"rows": rows, "total": len(rows), "totalPage": 1}}


def test_date_comparisons_use_explicit_start_field_and_boundary_evidence():
    early = {"jobFairId": 1, "startDate": "2026-10-14"}
    later = {"jobFairId": 2, "startDate": "2026-11-01"}
    queries = [query(None, [early, later]), query("2026-10-13", [early, later]),
               query("2026-10-14", [early, later]), query("2026-10-15", [later])]
    evidence = date_evidence(queries, id_field="jobFairId", date_field="startDate")
    assert evidence["status"] == "observed_evidence"
    assert evidence["consistent_hypotheses"] == ["start_date_inclusive"]


@pytest.mark.parametrize("rows", [[], [{"jobFairId": 1}], [{"jobFairId": 1, "startDate": "unknown"}]])
def test_missing_date_evidence_is_never_confirmed(rows):
    queries = [query("2026-10-13", rows), query("2026-10-14", rows), query("2026-10-15", rows)]
    assert date_evidence(queries, id_field="jobFairId", date_field="startDate")["status"] == "unverified"


def test_cli_persists_failure_evidence_and_returns_nonzero(make_client, monkeypatch, tmp_path):
    client, _, _, _ = make_client([requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden")])
    monkeypatch.setattr("campus_recruitment.probe.RecruitmentClient", lambda **kwargs: client)
    assert main(["--output-dir", str(tmp_path), "--anchor-date", "2026-10-14"]) == 2
    report = json.loads((tmp_path / "verification.json").read_text())
    log = json.loads((tmp_path / "requests.jsonl").read_text())
    assert report["phase1_complete"] is False and log["error"] == "proxy_policy"
    assert (tmp_path / "verification.md").is_file()
