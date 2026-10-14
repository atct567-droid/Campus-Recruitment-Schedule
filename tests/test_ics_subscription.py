"""Subscription safety and public-source integration, independently parsed ICS."""

import copy
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from icalendar import Calendar

from campus_recruitment.client import COMPANY_PATH
from campus_recruitment.errors import ApiError
from campus_recruitment.ical import export_ics
from campus_recruitment.matcher import JobMatcher
from campus_recruitment.normalizer import Normalizer
from campus_recruitment.subscription import build_feed, collect_public_snapshot, normalize_snapshot, public_pages
from scripts.build_ics_feed import main
from scripts.validate_ics_feed import validate

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/school-public-2026-10-14"


@pytest.fixture
def snapshot():
    records = json.loads((EVIDENCE / "recruitment.json").read_text())
    listing = json.loads((EVIDENCE / "listing.json").read_text())
    return normalize_snapshot(records, Normalizer.from_file(ROOT / "configs/school-public-schema.json"), listing=listing)


def test_actual_public_snapshot_exports_all_46_and_oct15(snapshot):
    ics, state, metadata = build_feed(snapshot)
    events = Calendar.from_ical(ics).walk("VEVENT")
    assert len(events) == len(state["events"]) == metadata["events"] == 46
    assert len({str(event["UID"]) for event in events}) == 46
    assert sum(event.decoded("DTSTART").date() == date(2026, 10, 15) for event in events) == 9
    assert all(str(event["STATUS"]) == "TENTATIVE" for event in events)
    assert metadata["collection_complete"] is False
    assert metadata["incomplete_fair_ids"] == ["666"]
    assert "未收齐" in state["events"]["666"]["fair"]["calendar_description"]
    assert "电源研发储干" in state["events"]["675"]["fair"]["calendar_description"]
    assert "铭普" in state["events"]["675"]["fair"]["calendar_description"]
    for event in events:
        assert event.decoded("DTSTART").utcoffset() == timedelta(hours=8)
        assert sorted(alarm.decoded("TRIGGER") for alarm in event.subcomponents) == [timedelta(days=-1), timedelta(minutes=-30)]
    for item in state["events"].values():
        fair = item["fair"]
        expected = "https://bysjyw.guat.edu.cn/m/pages/jobfair/jobfair-detail?jobFairId=" + fair["job_fair_id"]
        assert fair["source_url"] == expected
        assert fair["calendar_description"].splitlines()[:3] == ["官网链接：", expected, ""]
    raw = ics.encode()
    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_same_snapshot_is_idempotent_and_reschedule_keeps_uid(snapshot):
    first, state, metadata = build_feed(snapshot)
    second, state2, metadata2 = build_feed(snapshot, state)
    assert (first, state, metadata) == (second, state2, metadata2)
    changed = copy.deepcopy(snapshot)
    fair = changed["fairs"][0]
    identifier = fair["job_fair_id"]
    fair["start_time"] = "2026-10-15T15:00:00+08:00"
    fair["end_time"] = "2026-10-15T18:00:00+08:00"
    changed["generated_at"] = "2026-10-14T13:00:00+08:00"
    updated, state3, _ = build_feed(changed, state)
    before = {str(event["UID"]): event for event in Calendar.from_ical(first).walk("VEVENT")}
    after = {str(event["UID"]): event for event in Calendar.from_ical(updated).walk("VEVENT")}
    assert before.keys() == after.keys()
    assert state3["events"][identifier]["revision"] == 1
    assert state3["events"][identifier]["updated_at"] == changed["generated_at"]
    assert sum(item["revision"] for item in state3["events"].values()) == 1


def test_missing_and_ambiguous_events_preserve_previous_without_cancellation(snapshot):
    first, previous, _ = build_feed(snapshot)
    absent = copy.deepcopy(snapshot)
    removed = absent["fairs"].pop()
    absent["fairs"][0]["end_time"] = None
    second, state, metadata = build_feed(absent, previous)
    assert state == previous
    assert first == second
    assert removed["job_fair_id"] in metadata["retained_unobserved_events"]
    assert len(metadata["skipped"]) == 1
    assert all(str(event["STATUS"]) != "CANCELLED" for event in Calendar.from_ical(second).walk("VEVENT"))
    new, _, info = build_feed(absent)
    assert len(Calendar.from_ical(new).walk("VEVENT")) == 44
    assert len(info["skipped"]) == 1


def test_unchanged_content_does_not_bump_revision_for_new_check_time(snapshot):
    _, old, _ = build_feed(snapshot)
    snapshot["generated_at"] = "2026-10-14T14:00:00+08:00"
    _, new, _ = build_feed(snapshot, old)
    assert old == new


def test_link_correction_keeps_uid_and_time_but_advances_modified_stamp(snapshot):
    first, old, _ = build_feed(snapshot)
    before = {str(event["UID"]): event for event in Calendar.from_ical(first).walk("VEVENT")}
    for fair in snapshot["fairs"]:
        fair["source_url"] = "https://bysjyw.guat.edu.cn/index"
    updated, state, metadata = build_feed(snapshot, old, published_at="2026-10-14T18:00:00+08:00")
    after = {str(event["UID"]): event for event in Calendar.from_ical(updated).walk("VEVENT")}
    assert before.keys() == after.keys()
    assert metadata["generated_at"] == snapshot["generated_at"]
    assert metadata["published_at"] == "2026-10-14T18:00:00+08:00"
    for identifier, event in after.items():
        assert event.decoded("DTSTART") == before[identifier].decoded("DTSTART")
        assert event.decoded("DTEND") == before[identifier].decoded("DTEND")
        assert int(event["SEQUENCE"]) == 1
        assert event.decoded("LAST-MODIFIED").isoformat() == "2026-10-14T10:00:00+00:00"
        assert str(event["URL"]) == "https://bysjyw.guat.edu.cn/index"
    repeated, state2, _ = build_feed(snapshot, state, published_at="2026-10-14T19:00:00+08:00")
    assert updated == repeated and state == state2


@pytest.mark.parametrize("timestamp", ["2026-10-14T12:00:00+08:00", "2026-10-14T18:00:00"])
def test_publication_time_must_be_explicit_and_not_older_than_source(snapshot, timestamp):
    with pytest.raises(ValueError, match="Publication timestamp"):
        build_feed(snapshot, published_at=timestamp)


@pytest.mark.parametrize("unsafe", ["synthetic", "private", "listing", "query", "position", "company"])
def test_unsafe_subscription_input_rejected(snapshot, unsafe):
    if unsafe == "synthetic":
        snapshot["provenance"] = "synthetic"
    elif unsafe == "private":
        snapshot["fairs"][0]["private"] = True
    elif unsafe == "listing":
        snapshot["listing_complete"] = False
    elif unsafe == "query":
        snapshot["query_semantics_verified"] = False
    else:
        snapshot["companies" if unsafe == "company" else "positions"][0]["provenance"] = "synthetic"
    with pytest.raises(ValueError):
        build_feed(snapshot)


def test_unverified_status_is_never_auto_confirmed_by_default(snapshot):
    ics, skipped = export_ics(snapshot["fairs"])
    assert not Calendar.from_ical(ics).walk("VEVENT")
    assert len(skipped) == 46


def test_verified_cancellation_only_updates_owned_stable_event(snapshot):
    first, old, _ = build_feed(snapshot)
    snapshot["fairs"][0].update(status="cancelled", status_verified=True)
    updated, _, _ = build_feed(snapshot, old)
    assert {str(event["UID"]) for event in Calendar.from_ical(first).walk("VEVENT")} == {
        str(event["UID"]) for event in Calendar.from_ical(updated).walk("VEVENT")}
    cancelled = [event for event in Calendar.from_ical(updated).walk("VEVENT") if str(event["STATUS"]) == "CANCELLED"]
    assert len(cancelled) == 1 and not cancelled[0].subcomponents
    snapshot["fairs"][0]["status_verified"] = False
    _, _, metadata = build_feed(snapshot)
    assert len(metadata["skipped"]) == 1


def test_build_script_and_independent_validation(tmp_path):
    args = ["--snapshot", str(EVIDENCE / "recruitment.json"), "--listing", str(EVIDENCE / "listing.json"), "--output", str(tmp_path)]
    assert main(args) == 0
    assert validate(tmp_path)["events"] == 46
    raw = (tmp_path / "recruitment.ics").read_bytes()
    assert main(args) == 0
    assert raw == (tmp_path / "recruitment.ics").read_bytes()
    bad = json.loads((EVIDENCE / "listing.json").read_text())
    bad["listing_complete"] = False
    path = tmp_path / "bad-listing.json"
    path.write_text(json.dumps(bad))
    args[3] = str(path)
    with pytest.raises(ValueError):
        main(args)
    assert raw == (tmp_path / "recruitment.ics").read_bytes()


def test_independent_validation_rejects_link_to_a_different_fair(tmp_path):
    main(["--snapshot", str(EVIDENCE / "recruitment.json"), "--listing", str(EVIDENCE / "listing.json"), "--output", str(tmp_path)])
    path = tmp_path / "recruitment.ics"
    calendar = Calendar.from_ical(path.read_bytes())
    calendar.walk("VEVENT")[0]["URL"] = "https://bysjyw.guat.edu.cn/m/pages/jobfair/jobfair-detail?jobFairId=999999"
    path.write_bytes(calendar.to_ical())
    with pytest.raises(ValueError, match="source URL mismatch"):
        validate(tmp_path)


def test_partial_public_pages_stop_before_page_6():
    calls = []

    class Client:
        def get_json(self, path, params):
            calls.append(params["pageNum"])
            return {"rows": [{"id": params["pageNum"]}], "total": 9, "totalPage": 9}

    rows, complete, error = public_pages(Client(), COMPANY_PATH, "666")
    assert calls == [1, 2, 3, 4, 5]
    assert len(rows) == 5 and complete is False and error["kind"] == "public_page_limit"


def test_denied_public_page_stops_without_retry_or_claiming_complete():
    calls = []

    class Client:
        def get_json(self, path, params):
            calls.append(params["pageNum"])
            if len(calls) == 2:
                raise ApiError("http", "Unexpected HTTP status", status=400)
            return {"rows": [{"id": 1}], "total": 3, "totalPage": 3}

    rows, complete, error = public_pages(Client(), COMPANY_PATH, "666")
    assert calls == [1, 2] and len(rows) == 1 and complete is False
    assert error["http_status"] == 400


def test_daily_semantic_drift_fails_before_subrequests():
    class Client:
        def list_fairs(self, day, page_size):
            return SimpleNamespace(rows=[{"jobFairId": 1, "startTime": "2026-10-16 15:00:00"}])

    normalizer = Normalizer.from_file(ROOT / "configs/school-public-schema.json")
    with pytest.raises(ApiError, match="different or missing date"):
        collect_public_snapshot(Client(), normalizer, JobMatcher.from_file(ROOT / "configs/matching.json"),
                                start=date(2026, 10, 15), days=1)


def test_collection_opt_in_required_before_school_access(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_REAL_COLLECTION", raising=False)
    with pytest.raises(ValueError, match="ENABLE_REAL_COLLECTION"):
        main(["--collect", "--output", str(tmp_path)])
    assert not (tmp_path / "recruitment.ics").exists()


@pytest.mark.parametrize("fail_detail", [False, True])
def test_mock_collection_retains_fair_when_subdetails_fail(fail_detail):
    records = json.loads((EVIDENCE / "recruitment.json").read_text())
    fair, company, position = records["fairs"][0], records["companies"][0], records["positions"][0]

    class Client:
        def list_fairs(self, day, page_size):
            return SimpleNamespace(rows=[fair])

        def fair_detail(self, fair_id):
            if fail_detail:
                raise ApiError("http", "Unexpected HTTP status", status=400)
            return fair

        def get_json(self, path, params):
            return {"rows": [company if path == COMPANY_PATH else position], "total": 1, "totalPage": 1}

    snapshot, diagnostics = collect_public_snapshot(
        Client(), Normalizer.from_file(ROOT / "configs/school-public-schema.json"),
        JobMatcher.from_file(ROOT / "configs/matching.json"), start=date(2026, 10, 14), days=1)
    ics, _, metadata = build_feed(snapshot)
    assert len(Calendar.from_ical(ics).walk("VEVENT")) == 1
    assert bool(diagnostics) == fail_detail
    assert metadata["collection_complete"] == (not fail_detail)
    if fail_detail:
        assert "未收齐" in str(Calendar.from_ical(ics).walk("VEVENT")[0]["DESCRIPTION"])


@pytest.mark.parametrize("problem", ["repeat", "metadata_change", "empty"])
def test_bad_public_pagination_cannot_claim_complete(problem):
    class Client:
        def get_json(self, path, params):
            number = params["pageNum"]
            row = {"id": 1 if problem == "repeat" else number}
            return {"rows": [] if problem == "empty" and number == 2 else [row],
                    "total": 4 if problem == "metadata_change" and number == 2 else 3, "totalPage": 3}

    _, complete, error = public_pages(Client(), COMPANY_PATH, "666")
    assert complete is False and error["kind"] == "pagination"
