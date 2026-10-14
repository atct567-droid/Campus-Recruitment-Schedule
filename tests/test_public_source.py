"""Public page context must preserve anonymous preparation and network controls."""

from datetime import date
from pathlib import Path

import pytest
from conftest import response

from campus_recruitment.client import BASE_URL, LIST_PATH, Collection, RecruitmentClient
from campus_recruitment.collector import QueryPlan, collect
from campus_recruitment.normalizer import Normalizer


def test_public_referer_does_not_import_session_credentials(make_client):
    client, session, _, _ = make_client([response({"code": 200, "rows": [], "total": 0})],
                                      public_referer=BASE_URL + "/jobfairSearch")
    session.auth = ("user", "must-not-send")
    session.cookies.set("session", "must-not-send")
    result = client.list_fairs("2026-10-14")
    assert result.rows == []
    prepared, options = session.sent[0]
    assert prepared.headers["Referer"] == BASE_URL + "/jobfairSearch"
    assert prepared.headers["Accept"] == "application/json"
    assert "Authorization" not in prepared.headers and "Cookie" not in prepared.headers
    assert options["verify"] is True and options["allow_redirects"] is False
    assert options["proxies"] == {"https": "http://configured-proxy:8080"}


@pytest.mark.parametrize("referer", [
    "https://other.example/jobfairSearch", "http://bysjyw.guat.edu.cn/jobfairSearch",
    "https://user:password@bysjyw.guat.edu.cn/jobfairSearch",
    BASE_URL + "/admin", BASE_URL + "/jobfairSearch?token=invalid",
])
def test_refuse_non_public_or_different_origin_context(referer):
    with pytest.raises(ValueError):
        RecruitmentClient(public_referer=referer)


def test_default_transport_keeps_no_referer(make_client):
    client, session, _, _ = make_client([response({"code": 200, "rows": []})])
    client.get_json(LIST_PATH)
    assert "Referer" not in session.sent[0][0].headers


def test_changed_daily_query_semantics_cannot_claim_complete_coverage():
    class ChangedAPI:
        def list_fairs(self, query, page_size):
            return Collection([{"jobFairId": 991, "startTime": "2026-10-15 09:00:00"}], 1, 1, 1, ["rows"])

        def fair_detail(self, identifier):
            raise AssertionError("Must stop before details when the date contract changed")

    schema = Normalizer.from_file(Path(__file__).resolve().parents[1] / "configs/school-public-schema.json")
    snapshot = collect(ChangedAPI(), schema, QueryPlan(date(2026, 10, 14), date(2026, 10, 15), "daily", verified=True))
    assert snapshot.complete is False and not snapshot.fairs
    assert snapshot.errors[0]["kind"] == "query_semantics"


def test_reviewed_detail_template_uses_stable_fair_id_for_fair_and_position():
    config = Normalizer.from_file(Path(__file__).resolve().parents[1] / "configs/school-public-schema.json").config
    config["fair_source_url_template"] = BASE_URL + "/jobfairDetail/{job_fair_id}"
    schema = Normalizer(config)
    expected = BASE_URL + "/jobfairDetail/675"
    assert schema.fair({"jobFairId": 675}).source_url == expected
    assert schema.position({"positionId": 1, "companyId": 2}, "675").source_url == expected


@pytest.mark.parametrize("template", [
    BASE_URL + "/jobfairDetail/{unknown}", BASE_URL + "/jobfairDetail/{job_fair_id}/{job_fair_id}",
    BASE_URL + "/jobfairDetail/{job_fair_id}?token=private", "https://user:secret@example.com/{job_fair_id}",
    "javascript:alert({job_fair_id})", BASE_URL + "/jobfairDetail/{job_fair_id}\ninvalid",
])
def test_invalid_source_templates_rejected(template):
    config = Normalizer.from_file(Path(__file__).resolve().parents[1] / "configs/school-public-schema.json").config
    config["fair_source_url_template"] = template
    with pytest.raises(ValueError, match="source URL template"):
        Normalizer(config)
