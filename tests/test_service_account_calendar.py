import json
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from conftest import FakeClock, ScriptedSession, response

from campus_recruitment.calendar_sync import (
    SCOPES,
    CalendarError,
    GoogleCalendarAPI,
    MockCalendarAPI,
    SyncEngine,
    event_body,
    event_id,
)
from campus_recruitment.cli import main
from campus_recruitment.storage import Store, calendar_scope

TARGET = "synthetic-target@group.calendar.google.com"


def fair():
    return {"job_fair_id": "666", "title": "合成综合双选会", "start_time": "2026-10-15T09:00:00+08:00",
            "end_time": "2026-10-15T12:00:00+08:00", "location": "合成主会场", "source_url": "https://bysjyw.guat.edu.cn/jobfairSearch",
            "status": "scheduled", "status_verified": True, "provenance": "synthetic", "revision": 0}


def api(outcomes, **options):
    session, clock = ScriptedSession(outcomes), FakeClock()
    adapter = GoogleCalendarAPI(SimpleNamespace(valid=True, token="synthetic-only-token"), session=session,
                                sleep=clock.sleep, monotonic=clock.monotonic, calendar_id=TARGET, **options)
    return adapter, session


def service_info():
    # This deliberately invalid marker is never a real private key; the SDK is mocked.
    return {"type": "service_account", "client_email": "synthetic@sample.iam.gserviceaccount.com",
            "private_key": "SYNTHETIC-KEY-MUST-NEVER-BE-LOGGED", "token_uri": "https://oauth2.googleapis.com/token"}


def test_prefer_service_account_without_oauth_login(monkeypatch):
    seen = []
    def create(info, *, scopes):
        seen.append((info["type"], scopes))
        return SimpleNamespace(valid=False, token=None)
    monkeypatch.setattr("google.oauth2.service_account.Credentials.from_service_account_info", create)
    monkeypatch.setattr("google.oauth2.credentials.Credentials.from_authorized_user_info", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("OAuth fallback")))
    monkeypatch.setenv("GOOGLE_CREDENTIALS", json.dumps(service_info()))
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_JSON", '{"refresh_token":"synthetic-unused"}')
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", TARGET)
    loaded = GoogleCalendarAPI.from_environment()
    assert loaded.auth_kind == "service_account" and loaded.calendar_id == TARGET
    assert seen == [("service_account", SCOPES)]
    assert loaded.attempts == 0  # Credential loading is not a network/access verification.
    loaded.close()


@pytest.mark.parametrize("credential", ["not JSON", "[]", '{"type":"service_account","private_key":"SYNTHETIC-KEY-MUST-NEVER-BE-LOGGED"}'],
                         ids=["invalid-json", "wrong-json-root", "missing-service-fields"])
def test_bad_credentials_do_not_echo_private_material(monkeypatch, tmp_path, capsys, credential):
    monkeypatch.setenv("GOOGLE_CREDENTIALS", credential)
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", TARGET)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_JSON", raising=False)
    assert main(["calendar-check", "--output-dir", str(tmp_path)]) == 2
    combined = capsys.readouterr().out + (tmp_path / "failure.json").read_text()
    assert "SYNTHETIC-KEY" not in combined and "private_key" not in combined and credential not in combined


def test_sdk_signer_errors_are_suppressed(monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS", json.dumps(service_info()))
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", TARGET)
    def fail(*a, **kw):
        raise ValueError("SYNTHETIC-KEY-MUST-NEVER-BE-LOGGED")
    monkeypatch.setattr("google.oauth2.service_account.Credentials.from_service_account_info", fail)
    with pytest.raises(CalendarError) as caught:
        GoogleCalendarAPI.from_environment()
    assert "SYNTHETIC-KEY" not in str(caught.value)


@pytest.mark.parametrize("role", ["writer", "owner", "writerWithoutPrivateAccess"])
def test_access_preflight_reads_only_target_metadata(role):
    adapter, session = api([response({"accessRole": role, "timeZone": "Asia/Shanghai"})])
    checked = adapter.check_access(TARGET)
    assert checked["writable"] and checked["status"] == "verified"
    prepared, settings = session.sent[0]
    assert prepared.method == "GET" and unquote(urlsplit(prepared.url).path) == f"/calendar/v3/calendars/{TARGET}/events"
    assert parse_qs(urlsplit(prepared.url).query) == {"maxResults": ["1"], "fields": ["accessRole,timeZone"]}
    assert settings["verify"] and settings["allow_redirects"] is False


@pytest.mark.parametrize("role", ["reader", "freeBusyReader", "none", None])
def test_access_without_writer_role_fails_before_events(role):
    adapter, session = api([response({"accessRole": role})])
    with pytest.raises(CalendarError) as caught:
        adapter.check_access(TARGET)
    assert caught.value.kind == "calendar_permission" and len(session.sent) == 1


@pytest.mark.parametrize("status", [403, 404])
def test_inaccessible_calendar_does_not_fall_back_to_primary(status):
    adapter, session = api([response(status=status)])
    with pytest.raises(CalendarError):
        adapter.check_access(TARGET)
    assert len(session.sent) == 1


def test_scope_bound_to_first_configured_calendar(monkeypatch, tmp_path):
    adapter, session = api([])
    for operation in (adapter.get, adapter.cancel):
        with pytest.raises(CalendarError, match="configured"):
            operation("another@group.calendar.google.com", "event")
    with pytest.raises(CalendarError):
        adapter.check_access("primary")
    assert not session.sent
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", TARGET)
    assert main(["sync", "--db", str(tmp_path / "db"), "--calendar-id", "other", "--output-dir", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "db").exists()


def test_event_preview_companies_jobs_location_link_and_two_reminders(tmp_path):
    companies = [{"company_id": f"c{i}", "company_name": f"合成企业{i}", "related_job_fair_ids": ["666"]} for i in range(200)]
    positions = [{"job_fair_id": "666", "company_id": "c1", "position_id": "p1", "position_name": "嵌入式工程师", "matching_score": 90}]
    with Store(tmp_path / "db") as store:
        result = SyncEngine(store, calendar_id=TARGET).sync([fair()], positions, companies)
        assert len(result["actions"]) == 1 and result["access_check"]["status"] == "not_checked"
        body = result["actions"][0]["event_preview"]
        assert body["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 30}]}
        for expected in ("合成企业1", "嵌入式工程师", "合成主会场", "https://bysjyw.guat.edu.cn/jobfairSearch", "共 200 家"):
            assert expected in body["description"]
        assert not store.mapping(TARGET, "666")


def test_mock_write_permission_checked_before_any_mutation(tmp_path):
    with Store(tmp_path / "db") as store:
        denied = MockCalendarAPI(access_role="reader")
        result = SyncEngine(store, denied, calendar_id=TARGET, dry_run=False).sync([fair()])
        assert result["errors"][0]["kind"] == "calendar_permission"
        assert [call[0] for call in denied.calls] == ["check_access"]
        assert not store.mapping(TARGET, "666")


def test_readonly_remote_dry_run_reconciles_without_changing_state(tmp_path):
    remote = MockCalendarAPI()
    body, _ = event_body(fair(), [])
    remote.events[TARGET, event_id("666")] = {"id": event_id("666"), **body}
    with Store(tmp_path / "db") as store:
        result = SyncEngine(store, remote, calendar_id=TARGET, read_remote=True).sync([fair()])
        assert result["actions"][0]["action"] == "noop" and result["access_check"]["status"] == "verified_mock"
        assert not store.mapping(TARGET, "666") and not result["real_sync_performed"]
        assert all(call[0] in {"check_access", "get"} for call in remote.calls)


def test_manual_drift_updated_without_duplicate_and_cancel_mapping_recovery(tmp_path):
    remote = MockCalendarAPI()
    with Store(tmp_path / "db") as store:
        engine = SyncEngine(store, remote, calendar_id=TARGET, dry_run=False)
        engine.sync([fair()])
        remote.events[TARGET, event_id("666")]["location"] = "合成修改地点"
        assert engine.sync([fair()])["actions"][0]["action"] == "update"
        assert len(remote.events) == 1
        store.db.execute("DELETE FROM calendar_map")
        store.db.commit()
        cancelled = {**fair(), "status": "cancelled"}
        assert engine.sync([cancelled])["actions"][0]["action"] == "cancel"
        assert store.mapping(TARGET, "666")["status"] == "cancelled"


def test_persistent_hashed_mapping_survives_sanitized_runner_backup(tmp_path):
    with Store(tmp_path / "private.db") as store:
        store.save_mapping(TARGET, "666", event_id("666"), "digest", 1, "scheduled")
        store.backup(tmp_path / "cache.db", public=True, include_calendar_map=True)
    assert TARGET.encode() not in (tmp_path / "cache.db").read_bytes()
    with Store(tmp_path / "cache.db") as restored:
        assert restored.mapping(TARGET, "666")["event_id"] == event_id("666")
        assert restored.mapping("other", "666") is None
        assert restored.mapping(TARGET, "666")["calendar_key"] == calendar_scope(TARGET)


def test_existing_raw_calendar_keys_migrate_to_hashes(tmp_path):
    path = tmp_path / "legacy.db"
    with Store(path) as store:
        store.db.execute("INSERT INTO calendar_map VALUES(?,?,?,?,?,?)", (TARGET, "666", "event", "digest", 0, "scheduled"))
        store.db.commit()
    with Store(path) as migrated:
        assert migrated.mapping(TARGET, "666")["event_id"] == "event"
        row = migrated.db.execute("SELECT calendar_key FROM calendar_map").fetchone()
        assert row[0] == calendar_scope(TARGET)


def test_cancel_refuses_unrelated_owned_calendar_event(tmp_path):
    remote = MockCalendarAPI()
    remote.events[TARGET, event_id("666")] = {"id": event_id("666"), "summary": "合成的他人日程"}
    with Store(tmp_path / "db") as store:
        result = SyncEngine(store, remote, calendar_id=TARGET, dry_run=False).sync([{**fair(), "status": "cancelled"}])
        assert result["errors"][0]["kind"] == "ownership"
        assert not any(call[0] == "cancel" for call in remote.calls)


def test_readonly_cli_preflight_never_claims_no_network(tmp_path, monkeypatch, capsys):
    # The source contract and adapter are synthetic; the trusted-source label only
    # exercises the production gate. No actual school/Google request is made.
    import campus_recruitment.models as models
    event = models.Fair("666", title="合成活动", start_time=fair()["start_time"], end_time=fair()["end_time"],
                        status="scheduled", status_verified=True, provenance="school_api")
    with Store(tmp_path / "db") as store:
        store.apply(models.Snapshot(fairs=[event], checked_at="2026-10-14T08:00:00+08:00"))
    mocked = MockCalendarAPI()
    mocked.close = lambda: None
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", TARGET)
    monkeypatch.setattr("campus_recruitment.cli.GoogleCalendarAPI.from_environment", lambda *a, **kw: mocked)
    assert main(["sync", "--db", str(tmp_path / "db"), "--output-dir", str(tmp_path / "out"), "--check-access"]) == 0
    console = capsys.readouterr().out
    assert "只读" in console and "没有读取凭据" not in console
    assert all(call[0] in {"check_access", "get"} for call in mocked.calls)
