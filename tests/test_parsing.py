import pytest

from campus_recruitment.errors import ApiError
from campus_recruitment.parsing import parse_detail, parse_page


@pytest.mark.parametrize("payload,layout", [
    ({"rows": [{"id": 1}], "total": "1", "totalPage": "1"}, "rows"),
    ({"data": {"rows": [{"id": 1}], "total": 1, "totalPage": 1}}, "data.rows"),
    ({"data": [{"id": 1}], "total": 1, "totalPage": 1}, "data"),
    ({"data": {"rows": [{"id": 1}]}, "total": 1, "totalPage": 1}, "data.rows"),
])
def test_response_layouts(payload, layout):
    page = parse_page(payload)
    assert (page.rows, page.total, page.total_page, page.layout) == ([{"id": 1}], 1, 1, layout)


@pytest.mark.parametrize("payload", [
    {"rows": []}, {"data": []}, {"data": {"rows": []}},
    {"rows": None, "total": 0}, {"data": None, "total": 0, "totalPage": 0},
])
def test_explicit_empty_results(payload):
    assert parse_page(payload).rows == []


@pytest.mark.parametrize("payload", [
    {}, {"data": None}, {"rows": None}, {"data": {}}, {"rows": [1]},
    {"rows": {}, "total": 0}, {"rows": [], "total": -1},
    {"rows": [], "total": True}, {"rows": [], "total": "1.5"},
    {"rows": [{"id": 1}], "total": 0},
    {"rows": [], "total": 0, "totalPage": 2},
    {"rows": [], "data": {"rows": [{"id": 1}]}},
    {"rows": [], "total": 0, "data": {"total": 1}},
])
def test_unknown_or_conflicting_schema_is_not_empty_success(payload):
    with pytest.raises(ApiError, match=".") as caught:
        parse_page(payload)
    assert caught.value.kind == "schema"


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {}}, {"data": []}])
def test_absent_details_fail(payload):
    with pytest.raises(ApiError):
        parse_detail(payload)


def test_detail_object():
    assert parse_detail({"data": {"jobFairId": 1}}) == {"jobFairId": 1}
