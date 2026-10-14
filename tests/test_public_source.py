"""Public page context must preserve anonymous preparation and network controls."""

import pytest
from conftest import response

from campus_recruitment.client import BASE_URL, LIST_PATH, RecruitmentClient


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
