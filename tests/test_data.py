import copy
from datetime import date
import json
from pathlib import Path

import pytest

from campus_recruitment.collector import QueryPlan, collect
from campus_recruitment.errors import ApiError
from campus_recruitment.fixtures import fixture_client
from campus_recruitment.models import Snapshot
from campus_recruitment.normalizer import Normalizer, parse_time, public_url
from campus_recruitment.storage import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def schema():
    return Normalizer.from_file(ROOT / "configs/synthetic-schema.json")


@pytest.fixture
def fixture_data():
    return json.loads((ROOT / "fixtures/synthetic-recruitment.json").read_text())


@pytest.mark.parametrize("value,expected", [
    ("2026年10月15日 09:30", "2026-10-15T09:30:00+08:00"),
    ("2026-10-15T01:30:00Z", "2026-10-15T09:30:00+08:00"),
    ("2026-10-15 09:30", "2026-10-15T09:30:00+08:00"),
    ("2026-10-15", None), ("unknown", None), (1730000000, None),
])
def test_explicit_time_only(value, expected):
    assert parse_time(value)[0] == expected


def test_missing_fields_are_not_invented(schema):
    fair = schema.fair({"fair_id": 666, "start": "2026-10-15"})
    assert fair.title is None and fair.start_time is None and fair.end_time is None
    assert fair.start_date == "2026-10-15" and fair.status == "unknown"
    assert fair.provenance == "synthetic"
    position = schema.position({"position_id": 1, "company_id": 2, "count": "若干"}, "666")
    assert position.recruitment_count is None


def test_missing_id_rejected(schema):
    with pytest.raises(ApiError):
        schema.company({"company_name": "同名企业"}, "666")


def test_invalid_interval_rejected(schema):
    fair = schema.fair({"fair_id": 1, "start": "2026-10-15 12:00", "end": "2026-10-15 11:00"})
    assert fair.end_time is None


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://user:secret@example.com", "https://example.com?token=secret"])
def test_unsafe_source_links(value):
    assert public_url(value) is None


def test_query_strategies_are_explicit():
    start, end = date(2026, 12, 30), date(2027, 1, 2)
    assert QueryPlan(start, end).dates() == [None]
    assert QueryPlan(start, end, "daily").dates() == ["2026-12-30", "2026-12-31", "2027-01-01"]
    assert QueryPlan(start, end, "monthly").dates() == ["2026-12-01", "2027-01-01"]
    with pytest.raises(ValueError):
        QueryPlan(start, end, "daily", max_queries=2).dates()


def test_collect_production_pagination_and_window(schema, fixture_data):
    client = fixture_client(fixture=fixture_data)
    snapshot = collect(client, schema, QueryPlan(date(2026, 10, 14), date(2026, 11, 13), verified=True), page_size=2)
    assert not snapshot.errors and snapshot.complete
    assert {f.job_fair_id for f in snapshot.fairs} == {"666", "667", "668", "669", "670"}
    assert len(snapshot.positions) > 4
    paths = client.session.sent
    assert any(x["path"].endswith("positionList") and x["params"]["pageNum"] == "2" for x in paths)
    assert any(x["path"].endswith("companyGroupList") and x["params"]["pageNum"] == "2" for x in paths)


def test_unverified_query_warns_and_history_lookup_works(schema, fixture_data):
    client = fixture_client(fixture=fixture_data)
    snapshot = collect(client, schema, QueryPlan(date(2026, 10, 14), date(2026, 11, 13)), fair_ids=["671"])
    assert not snapshot.complete and snapshot.warnings
    assert snapshot.fairs[0].job_fair_id == "671"


@pytest.mark.parametrize("fault", [
    {"status": 403}, {"status": 404}, {"status": 429}, {"status": 500}, {"status": 502},
    {"exception": "proxy403"}, {"exception": "timeout"},
    {"payload": {"code": 500}}, {"payload": {"code": 200}},
])
def test_collect_faults_preserve_incomplete_state(schema, fixture_data, fault):
    fixture_data["faults"] = [{"path": "/api/view/jobFair/list", "remaining": 3, **fault}]
    client = fixture_client(fixture=fixture_data)
    snapshot = collect(client, schema, QueryPlan(date(2026, 10, 14), date(2026, 11, 13), verified=True))
    assert snapshot.errors and not snapshot.complete and not snapshot.fairs


def test_record_limit(schema, fixture_data):
    client = fixture_client(fixture=fixture_data, max_records=1)
    snapshot = collect(client, schema, QueryPlan(date(2026, 10, 14), date(2026, 11, 13)))
    assert snapshot.errors[0]["kind"] == "record_limit"


def test_store_idempotency_same_names_distinct_ids_and_history(tmp_path, schema, fixture_data):
    snapshot = collect(fixture_client(fixture=fixture_data), schema,
                       QueryPlan(date(2026, 10, 14), date(2026, 11, 13), verified=True))
    with Store(tmp_path / "state.db") as store:
        _, added = store.apply(snapshot)
        count = len(store.history())
        _, changes = store.apply(snapshot)
        assert added and not changes and len(store.history()) == count
        assert len(store.list("fair")) == 5
        same_names = [c for c in store.list("company") if c["company_name"] == "合成电子科技"]
        assert len(same_names) == 2
        fair = copy.deepcopy(snapshot.fairs[0])
        fair.start_time = "2026-10-15T10:00:00+08:00"
        store.apply(Snapshot(fairs=[fair], checked_at="2026-10-15T08:00:00+08:00"))
        assert store.history()[-1]["change_type"] == "rescheduled"
        fair.status = "cancelled"
        fair.status_verified = True
        store.apply(Snapshot(fairs=[fair], checked_at="2026-10-15T09:00:00+08:00"))
        assert store.history()[-1]["change_type"] == "cancelled"
        # Missing fairs, including a failed empty snapshot, never cause inferred cancellations.
        store.apply(Snapshot(errors=[{"kind": "timeout"}], checked_at="2026-10-16T08:00:00+08:00"))
        assert len(store.list("fair")) == 5
        assert store.list("fair")[1]["status"] == "scheduled"


def test_conflicting_duplicates_rollback(tmp_path, schema):
    fair = schema.fair({"fair_id": 1})
    different = copy.deepcopy(fair)
    different.title = "冲突标题"
    with Store(tmp_path / "state.db") as store:
        with pytest.raises(ValueError):
            store.apply(Snapshot(fairs=[fair, different], checked_at=fair.last_checked_at))
        assert store.list("fair") == []
