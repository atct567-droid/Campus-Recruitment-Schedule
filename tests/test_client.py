from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from campus_recruitment.client import COMPANY_PATH, LIST_PATH, POSITION_PATH, RecruitmentClient
from campus_recruitment.errors import ApiError
from conftest import response


def page(rows, **metadata):
    return response({"code": 200, "rows": rows, **metadata})


def test_anonymous_preparation_keeps_proxy_and_tls(make_client, monkeypatch):
    client, session, _, events = make_client([page([])])
    session.auth = ("must-not-send", "secret")
    session.cookies.set("login", "must-not-send")
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *a: (_ for _ in ()).throw(AssertionError("netrc accessed")))
    client.get_json(LIST_PATH)
    prepared, settings = session.sent[0]
    assert "Authorization" not in prepared.headers and "Cookie" not in prepared.headers
    assert settings["verify"] is True and settings["allow_redirects"] is False
    assert settings["timeout"] == (5.0, 20.0)
    assert settings["proxies"] == {"https": "http://configured-proxy:8080"}
    assert events[0]["business_code"] == 200


@pytest.mark.parametrize("status,kind", [(401, "access_denied"), (403, "access_denied"), (407, "access_denied"),
                                         (302, "redirect"), (404, "http")])
def test_http_failure_never_retries_or_succeeds(make_client, status, kind):
    client, session, _, events = make_client([response(status=status)])
    with pytest.raises(ApiError) as caught:
        client.get_json(LIST_PATH)
    assert (caught.value.kind, caught.value.status) == (kind, status)
    assert len(session.sent) == 1 and events[-1]["retry"] is False


@pytest.mark.parametrize("payload", [{}, {"code": 0}, {"code": 500}, {"code": True}, {"code": None}, {"code": {}}, []])
def test_business_code_requires_explicit_acceptance(make_client, payload):
    client, session, _, _ = make_client([response(payload)])
    with pytest.raises(ApiError):
        client.get_json(LIST_PATH)
    assert len(session.sent) == 1


def test_success_code_can_be_explicitly_configured(make_client):
    client, _, _, _ = make_client([response({"code": "0", "rows": []})], success_codes=(0,))
    assert client.list_fairs().rows == []


def test_html_success_is_invalid_json(make_client):
    client, _, _, _ = make_client([response(body=b"<html>login</html>")])
    with pytest.raises(ApiError) as caught:
        client.get_json(LIST_PATH)
    assert caught.value.kind == "invalid_json"


def test_timeout_retries_are_limited_and_logged(make_client):
    client, session, clock, events = make_client([requests.ReadTimeout("timeout")] * 3)
    with pytest.raises(ApiError) as caught:
        client.get_json(LIST_PATH)
    assert caught.value.kind == "timeout" and len(session.sent) == 3
    assert clock.sleeps == [2, 4]
    assert [event["retry"] for event in events] == [True, True, False]


def test_connection_can_recover(make_client):
    client, _, _, events = make_client([requests.ConnectionError("transient"), page([])])
    assert client.list_fairs().rows == []
    assert events[0]["error"] == "connection"


@pytest.mark.parametrize("error,kind", [
    (requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden"), "proxy_policy"),
    (requests.exceptions.SSLError("certificate verify failed"), "tls"),
])
def test_no_retry_on_proxy_policy_or_tls_failure(make_client, error, kind):
    client, session, _, _ = make_client([error])
    with pytest.raises(ApiError) as caught:
        client.get_json(LIST_PATH)
    assert caught.value.kind == kind and len(session.sent) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_can_recover(make_client, status):
    client, _, clock, events = make_client([response(status=status, headers={"Retry-After": "3"}), page([])])
    assert client.list_fairs().rows == []
    assert clock.sleeps == [3] and events[0]["retry_delay_seconds"] == 3


@pytest.mark.parametrize("retry_after", ["120", "NaN", "invalid"])
def test_long_or_invalid_retry_after_stops(make_client, retry_after):
    client, session, _, _ = make_client([response(status=429, headers={"Retry-After": retry_after})])
    with pytest.raises(ApiError) as caught:
        client.get_json(LIST_PATH)
    assert caught.value.kind == "retry_after" and len(session.sent) == 1


def test_429_exhaustion_is_a_failure(make_client):
    client, session, _, _ = make_client([response(status=429)] * 3)
    with pytest.raises(ApiError) as caught:
        client.list_fairs()
    assert caught.value.status == 429 and len(session.sent) == 3


def test_sequential_calls_are_rate_limited(make_client):
    client, _, clock, _ = make_client([page([]), page([]), page([])])
    for _ in range(3):
        client.get_json(LIST_PATH)
    assert clock.sleeps == [2, 2]


def test_proxy_credentials_redacted(make_client):
    client, _, _, events = make_client([requests.exceptions.ProxyError("https://user:secret@proxy:443 403 Forbidden")])
    with pytest.raises(ApiError) as caught:
        client.get_json(LIST_PATH)
    assert "secret" not in str(caught.value) and "secret" not in str(events)


def test_complete_pagination_and_date_params(make_client):
    client, session, _, _ = make_client([
        page([{"id": 1}, {"id": 2}], total=3, totalPage=2),
        page([{"id": 3}], total=3, totalPage=2),
    ])
    result = client.list_fairs("2026-10-14", page_size=2)
    assert result.rows == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert result.pages_fetched == 2 and result.total == 3 and result.total_page == 2
    queries = [parse_qs(urlsplit(prepared.url).query) for prepared, _ in session.sent]
    assert queries == [{"calendarStartDate": ["2026-10-14"], "pageNum": [str(n)], "pageSize": ["2"]} for n in (1, 2)]


@pytest.mark.parametrize("metadata", [{"total": 3}, {"totalPage": 2}])
def test_either_pagination_count_is_supported(make_client, metadata):
    client, _, _, _ = make_client([page([{"id": 1}, {"id": 2}], **metadata), page([{"id": 3}], **metadata)])
    assert len(client.list_fairs(page_size=2).rows) == 3


def test_no_metadata_continues_even_after_short_page(make_client):
    client, session, _, _ = make_client([page([{"id": 1}]), page([{"id": 2}]), page([])])
    result = client.list_fairs(page_size=50)
    assert len(result.rows) == 2 and len(session.sent) == 3


def test_server_page_size_cap_does_not_truncate(make_client):
    client, session, _, _ = make_client([page([{"id": 1}], total=2), page([{"id": 2}], total=2)])
    assert len(client.list_fairs(page_size=50).rows) == 2 and len(session.sent) == 2


@pytest.mark.parametrize("metadata", [{"totalPage": 0}, {"totalPage": 1}, {"total": 0, "totalPage": 1}])
def test_empty_first_page_with_zero_or_one_total_pages(make_client, metadata):
    client, session, _, _ = make_client([page([], **metadata)])
    assert client.list_fairs().rows == [] and len(session.sent) == 1


@pytest.mark.parametrize("outcomes", [
    [page([{"id": 1}], total=2), page([], total=2)],
    [page([{"id": 1}], total=2), page([{"id": 1}], total=2)],
    [page([{"id": 1}, {"id": 1}], total=2)],
    [page([{"id": 1}], total=2), page([{"id": 2}], total=3)],
    [page([{"id": 1}], total=2), page([{"id": 2}])],
    [page([{"id": 1}], total=1, totalPage=2)],
    [page([{"id": 1}], total=2, totalPage=1)],
    [page([{"id": 1}, {"id": 2}], total=1)],
    [page([{"id": 1}], totalPage=2), page([], totalPage=2)],
])
def test_pagination_inconsistency_fails_without_partial_success(make_client, outcomes):
    client, _, _, _ = make_client(outcomes)
    with pytest.raises(ApiError) as caught:
        client.list_fairs()
    assert caught.value.kind == "pagination"


def test_failure_on_later_page_is_not_partial_success(make_client):
    client, _, _, _ = make_client([page([{"id": 1}], total=2), response(status=403)])
    with pytest.raises(ApiError) as caught:
        client.list_fairs()
    assert caught.value.kind == "access_denied"


@pytest.mark.parametrize("options,kind", [({"max_pages": 1}, "pagination"), ({"request_budget": 1}, "request_budget")])
def test_safety_limits_are_explicit_failures(make_client, options, kind):
    client, session, _, _ = make_client([page([{"id": 1}])], **options)
    with pytest.raises(ApiError) as caught:
        client.list_fairs()
    assert caught.value.kind == kind and len(session.sent) == 1


def test_all_four_endpoint_paths(make_client):
    client, session, _, _ = make_client([response({"code": 200, "data": {"jobFairId": "fair-1"}}),
                                           page([], total=0), page([], total=0)])
    assert client.fair_detail("fair-1")["jobFairId"] == "fair-1"
    assert client.positions("fair-1").rows == []
    assert client.companies("fair-1").rows == []
    assert [urlsplit(prepared.url).path for prepared, _ in session.sent] == ["/api/view/getJobFair/fair-1", POSITION_PATH, COMPANY_PATH]
    assert parse_qs(urlsplit(session.sent[1][0].url).query)["jobFairId"] == ["fair-1"]


@pytest.mark.parametrize("value", ["", "../1", "1?token=x", None, True])
def test_invalid_fair_ids_never_make_requests(make_client, value):
    client, session, _, _ = make_client([])
    with pytest.raises(ValueError):
        client.fair_detail(value)
    assert session.sent == []


@pytest.mark.parametrize("value", ["2026-2-1", "2026-02-30", "20261014", ""])
def test_date_format_validation(make_client, value):
    client, session, _, _ = make_client([])
    with pytest.raises(ValueError):
        client.list_fairs(value)
    assert session.sent == []


@pytest.mark.parametrize("options", [{"interval": 0}, {"retries": -1}, {"timeout": (0, 20)},
                                     {"request_budget": 0}, {"base_url": "http://example.com"},
                                     {"base_url": "https://user:pass@example.com"}])
def test_invalid_configuration(options):
    with pytest.raises(ValueError):
        RecruitmentClient(**options)
