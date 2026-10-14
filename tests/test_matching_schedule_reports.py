import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from icalendar import Calendar

from campus_recruitment.collector import QueryPlan, collect
from campus_recruitment.fixtures import fixture_client
from campus_recruitment.ical import export_ics
from campus_recruitment.matcher import JobMatcher
from campus_recruitment.models import Position, Snapshot
from campus_recruitment.normalizer import Normalizer
from campus_recruitment.reports import generate_reports
from campus_recruitment.schedule import Activity, conflicts, daily_order, load_personal
from campus_recruitment.storage import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("title,requirements,priority", [
    ("硬件工程师", "电路设计", "high"), ("助理硬件工程师", "", "high"),
    ("嵌入式软件开发", "STM32", "high"), ("Embedded Engineer", "RTOS", "high"),
    ("PCB工程师", "布局布线", "high"), ("STM32开发工程师", "C", "high"),
    ("单片机开发工程师", "MCU", "high"), ("FPGA工程师", "Verilog", "high"),
    ("电子工程师", "模拟电路", "high"), ("硬件测试工程师", "", "high"),
    ("电气工程师", "PLC", "medium"), ("自动化工程师", "", "medium"),
    ("测试工程师", "", "medium"), ("技术支持工程师", "", "medium"),
    ("研发工程师", "STM32 FPGA PCB", "high"),
    ("硬件销售", "STM32 FPGA PCB", "excluded"), ("软件测试工程师", "", "low"),
    ("财务文员", "嵌入式", "excluded"), ("软件工程师", "Web Python", "low"),
])
def test_explainable_matcher_false_positives(title, requirements, priority):
    matcher = JobMatcher.from_file(ROOT / "configs/matching.json")
    result = matcher.match(Position("p", "c", "f", title, requirements))
    assert result.matching_priority == priority and result.matching_reasons


def test_weights_configurable():
    config = json.loads((ROOT / "configs/matching.json").read_text())
    config["weights"]["high_title"] = 60
    result = JobMatcher(config).match(Position("p", "c", "f", "硬件工程师", ""))
    assert result.matching_priority == "medium" and result.matching_score == 60


def activity(identifier, start, end, kind="job_fair", **kwargs):
    return Activity(identifier, identifier, kind, start, end, **kwargs)


def test_half_open_conflicts_cross_day_missing_times_and_privacy():
    a = activity("a", "2026-10-15 23:00", "2026-10-16 01:00")
    b = activity("b", "2026-10-16 01:00", "2026-10-16 02:00")
    c = activity("私人面试", "2026-10-16 00:30", "2026-10-16 01:30", "interview", private=True)
    unknown = activity("unknown", "2026-10-16", None)
    cancelled = activity("cancelled", "2026-10-16 00:00", "2026-10-16 02:00", cancelled=True)
    assert conflicts([a, b, c, unknown, cancelled]) == []
    private = conflicts([a, b, c, unknown], include_private=True)
    assert len(private) == 2 and all(row["private"] for row in private)
    assert all(row["suggested_priority_id"] == "私人面试" for row in private)
    customized = conflicts([a, c], include_private=True, priorities={"job_fair": 110})
    assert customized[0]["suggested_priority_id"] == "a"
    assert daily_order([b, a])[0].identifier == "a"


def test_personal_file_must_be_private(tmp_path):
    path = tmp_path / "personal.json"
    path.write_text(json.dumps({"visibility": "private", "activities": [{"id": "i", "title": "面试", "kind": "interview", "start": "2026-10-15 10:00", "end": "2026-10-15 11:00"}]}))
    assert load_personal(path)[0].private
    path.write_text(json.dumps({"activities": []}))
    with pytest.raises(ValueError):
        load_personal(path)


def test_reports_ics_independent_validation_and_privacy(tmp_path):
    schema = Normalizer.from_file(ROOT / "configs/synthetic-schema.json")
    snapshot = collect(fixture_client(ROOT / "fixtures/synthetic-recruitment.json"), schema,
                       QueryPlan(date(2026, 10, 14), date(2026, 11, 13), verified=True))
    matcher = JobMatcher.from_file(ROOT / "configs/matching.json")
    for position in snapshot.positions:
        matcher.match(position)
    with Store(tmp_path / "state.db") as store:
        store.apply(snapshot)
        data = generate_reports(store, tmp_path / "public", as_of=date(2026, 10, 14), snapshot=snapshot)
        assert data["provenance"] == ["synthetic"]
        assert any(p["matching_priority"] == "high" for p in data["positions"])
        assert (tmp_path / "public/daily.md").is_file() and (tmp_path / "public/weekly.md").is_file()
        ics, skipped = export_ics(store.list("fair"), store.list("position"), stamp=datetime(2026, 10, 14, tzinfo=timezone.utc))
        parsed = Calendar.from_ical(ics.encode())
        events = parsed.walk("VEVENT")
        assert len(events) == 4 and len({str(e["UID"]) for e in events}) == 4
        assert skipped == [{"job_fair_id": "669", "reason": "时间不完整或活动状态未确认"}]
        assert any(str(e["STATUS"]) == "CANCELLED" for e in events)
        assert all(e.decoded("DTEND") > e.decoded("DTSTART") for e in events)
        assert all(e.decoded("DTSTART").utcoffset().total_seconds() == 8 * 3600 for e in events)
        assert "嵌入式软件开发" in str(events[0]["DESCRIPTION"])
        assert "," in str(events[0]["LOCATION"]) and "\\" in str(events[0]["LOCATION"])
        assert all(len(line) <= 75 for line in ics.encode().split(b"\r\n"))
        fair = copy.deepcopy(snapshot.fairs[0])
        fair.title = "中文" * 100 + ",分号;反斜线\\\n下一行"
        store.apply(Snapshot(fairs=[fair], checked_at="2026-10-15T08:00:00+08:00"))
        changed_ics, _ = export_ics(store.list("fair"))
        first = Calendar.from_ical(changed_ics.encode()).walk("VEVENT")[0]
        assert str(first["SUMMARY"]) == fair.title and int(first["SEQUENCE"]) == 1
        assert first["UID"] == events[0]["UID"]


def test_csv_formula_redaction_and_no_personal_fields(tmp_path):
    schema = Normalizer.from_file(ROOT / "configs/synthetic-schema.json")
    fair = schema.fair({"fair_id": 1, "title": "=HYPERLINK(恶意)", "start": "2026-10-15 09:00", "end": "2026-10-15 10:00", "state": "open"})
    position = schema.position({"position_id": 1, "company_id": 1, "name": "硬件工程师", "requirements": "13812345678 contact@example.com"}, "1")
    with Store(tmp_path / "state.db") as store:
        store.apply(Snapshot(fairs=[fair], positions=[position], checked_at=fair.last_checked_at))
        generate_reports(store, tmp_path / "public", as_of=date(2026, 10, 14))
        csv = (tmp_path / "public/fairs.csv").read_text(encoding="utf-8-sig")
        assert "'=HYPERLINK" in csv
        exported = (tmp_path / "public/recruitment.json").read_text()
        assert "13812345678" not in exported and "contact@example.com" not in exported
        assert "OAuth" not in exported and "private_activities" not in exported


def test_duplicate_fair_is_one_ics_event_and_private_rejected():
    fair = {"job_fair_id": "1", "start_time": "2026-10-15T09:00:00+08:00", "end_time": "2026-10-15T10:00:00+08:00", "status": "scheduled", "status_verified": True}
    ics, _ = export_ics([fair, fair])
    assert len(Calendar.from_ical(ics.encode()).walk("VEVENT")) == 1
    with pytest.raises(ValueError):
        export_ics([{**fair, "private": True}])
