import json
import runpy
from pathlib import Path

import pytest
import yaml

from campus_recruitment.cli import main
from campus_recruitment.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def driver():
    return runpy.run_path(str(ROOT / "scripts/run_calendar_workflow.py"))


@pytest.mark.parametrize("environment,expected", [
    ({}, False),
    ({"ENABLE_REAL_CALENDAR_SYNC": "true"}, False),
    ({"CALENDAR_TRIGGER": "manual", "CALENDAR_WRITE_REQUESTED": "true"}, False),
    ({"ENABLE_REAL_CALENDAR_SYNC": "true", "CALENDAR_TRIGGER": "manual", "CALENDAR_WRITE_REQUESTED": "true"}, True),
    ({"ENABLE_REAL_CALENDAR_SYNC": "true", "CALENDAR_TRIGGER": "schedule"}, False),
    ({"ENABLE_REAL_CALENDAR_SYNC": "true", "CALENDAR_TRIGGER": "schedule", "ENABLE_SCHEDULED_CALENDAR_SYNC": "true"}, True),
])
def test_explicit_manual_and_unattended_write_gates(environment, expected):
    assert driver()["requested_live"](environment) is expected


def test_missing_database_no_google_request(tmp_path, monkeypatch):
    monkeypatch.setattr("campus_recruitment.cli.GoogleCalendarAPI.from_environment", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network")))
    assert driver()["run"]({}, database=tmp_path / "db", cache=tmp_path / "cache", output=tmp_path / "out") == 0
    summary = json.loads((tmp_path / "out/calendar-workflow.json").read_text())
    assert summary["status"] == "skipped" and not summary["real_sync_performed"]


def test_missing_credentials_produces_complete_offline_preview(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "synthetic@group.calendar.google.com")
    assert main(["run", "--db", str(tmp_path / "db"), "--output-dir", str(tmp_path / "recruitment")]) == 0
    result = driver()["run"]({}, database=tmp_path / "db", cache=tmp_path / "cache", output=tmp_path / "out")
    assert result == 0
    summary = json.loads((tmp_path / "out/calendar-workflow.json").read_text())
    plan = json.loads((tmp_path / "out/calendar-dry-run.json").read_text())
    assert summary["status"] == "dry_run_completed" and not summary["real_sync_performed"]
    assert summary["access_check"]["status"] == "not_checked"
    assert any(action.get("event_preview", {}).get("reminders", {}).get("overrides") for action in plan["actions"])
    with Store(tmp_path / "cache") as cache:
        assert cache.list("fair")


def test_failed_latest_collection_blocks_unattended_write(tmp_path, monkeypatch):
    monkeypatch.setattr("campus_recruitment.cli.GoogleCalendarAPI.from_environment", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network")))
    with Store(tmp_path / "db"):
        pass
    result = driver()["run"]({"ENABLE_REAL_CALENDAR_SYNC": "true", "CALENDAR_TRIGGER": "schedule", "ENABLE_SCHEDULED_CALENDAR_SYNC": "true"},
                              database=tmp_path / "db", cache=tmp_path / "cache", output=tmp_path / "out")
    assert result == 0
    assert json.loads((tmp_path / "out/calendar-workflow.json").read_text())["status"] == "skipped"


def test_workflow_credentials_are_secrets_calendar_id_is_variable_and_dry_default():
    for name in ("google-calendar.yml", "calendar-access.yml", "recruitment.yml"):
        text = (ROOT / ".github/workflows" / name).read_text()
        assert "GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}" in text
        assert "GOOGLE_CALENDAR_ID: ${{ vars.GOOGLE_CALENDAR_ID }}" in text
        assert "secrets.GOOGLE_CALENDAR_ID" not in text
    workflow = yaml.load((ROOT / ".github/workflows/google-calendar.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["workflow_dispatch"]["inputs"]["enable_real_sync"]["default"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    hours = workflow["on"]["schedule"][0]["cron"].split()[1]
    assert {(int(h) + 8) % 24 for h in hours.split(",")} == {6, 12, 18}
    assert "ENABLE_SCHEDULED_CALENDAR_SYNC" in (ROOT / ".github/workflows/google-calendar.yml").read_text()
    access = yaml.load((ROOT / ".github/workflows/calendar-access.yml").read_text(), Loader=yaml.BaseLoader)
    commands = [step["run"] for step in access["jobs"]["access"]["steps"] if "run" in step]
    assert any("calendar-check" in command for command in commands)
    assert all("--enable-real-sync" not in command for command in commands)


@pytest.mark.parametrize("reported_write", [False, True])
def test_partial_failure_retains_mapping_and_reports_actual_partial_change(tmp_path, reported_write):
    # Synthetic protocol simulation only: no Google adapter/credential is used.
    from campus_recruitment.models import Snapshot
    module = driver()
    with Store(tmp_path / "db") as store:
        store.apply(Snapshot(complete=True, checked_at="2026-10-14T08:00:00+08:00"))
    def partial(args):
        with Store(tmp_path / "db") as store:
            store.save_mapping("synthetic-calendar", "666", "managed-event", "digest", 0, "scheduled")
        output = tmp_path / "out"
        output.mkdir(exist_ok=True)
        name = "calendar-sync.json" if reported_write else "calendar-dry-run.json"
        (output / name).write_text(json.dumps({"real_sync_performed": reported_write,
            "access_check": {"status": "verified_mock"}, "actions": [{"action": "create"}, {"action": "failed"}]}))
        return 2
    module["run"].__globals__["main"] = partial
    environment = {"ENABLE_REAL_CALENDAR_SYNC": "true", "CALENDAR_WRITE_REQUESTED": "true"} if reported_write else {}
    assert module["run"](environment, database=tmp_path / "db", cache=tmp_path / "cache", output=tmp_path / "out") == 2
    with Store(tmp_path / "cache") as store:
        assert store.mapping("synthetic-calendar", "666")["event_id"] == "managed-event"
    summary = json.loads((tmp_path / "out/calendar-workflow.json").read_text())
    assert summary["status"] == "failed" and summary["actions"]["failed"] == 1
    assert summary["real_sync_performed"] is reported_write and summary["access_check"]["status"] == "verified_mock"
