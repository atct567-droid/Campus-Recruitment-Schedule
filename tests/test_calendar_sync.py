from types import SimpleNamespace

import pytest
import requests
from conftest import FakeClock, ScriptedSession, response

from campus_recruitment.calendar_sync import (
    CalendarError,
    GoogleCalendarAPI,
    MockCalendarAPI,
    SyncEngine,
    event_id,
)
from campus_recruitment.storage import Store


def fair():
    return {"job_fair_id": "666", "title": "合成双选会", "start_time": "2026-10-15T09:00:00+08:00",
            "end_time": "2026-10-15T12:00:00+08:00", "location": "合成会场", "source_url": None,
            "status": "scheduled", "status_verified": True, "provenance": "synthetic", "revision": 0}


def test_dry_run_never_calls_api_or_advances_mapping(tmp_path):
    with Store(tmp_path / "state.db") as store:
        api = MockCalendarAPI()
        engine = SyncEngine(store, api)
        first, second = engine.sync([fair()]), engine.sync([fair()])
        assert first == second and first["actions"][0]["action"] == "create"
        assert not api.calls and store.mapping("dry-run", "666") is None
        assert first["real_sync_performed"] is False


def test_idempotent_create_reschedule_cancel_recovery_target_isolation(tmp_path):
    path = tmp_path / "state.db"
    api = MockCalendarAPI()
    with Store(path) as store:
        engine = SyncEngine(store, api, calendar_id="calendar-a", dry_run=False)
        event = fair()
        assert engine.sync([event])["actions"][0]["action"] == "create"
        assert engine.sync([event])["actions"][0]["action"] == "noop"
        event["start_time"] = "2026-10-15T10:00:00+08:00"
        event["revision"] = 1
        assert engine.sync([event])["actions"][0]["action"] == "update"
        assert len(api.events) == 1
        other = SyncEngine(store, api, calendar_id="calendar-b", dry_run=False)
        assert other.sync([event])["actions"][0]["action"] == "create"
        assert len(api.events) == 2
    with Store(path) as store:
        engine = SyncEngine(store, api, calendar_id="calendar-a", dry_run=False)
        assert engine.sync([event])["actions"][0]["action"] == "noop"
        event["status"] = "cancelled"
        assert engine.sync([event])["actions"][0]["action"] == "cancel"
        assert engine.sync([event])["actions"][0]["action"] == "noop"
        assert store.mapping("calendar-a", "666")["status"] == "cancelled"


def test_disconnect_after_remote_create_recovers_without_duplicate(tmp_path):
    class InterruptedAPI(MockCalendarAPI):
        def insert(self, calendar_id, identifier, body):
            super().insert(calendar_id, identifier, body)
            raise CalendarError("transport", "synthetic disconnect after commit")
    api = InterruptedAPI()
    with Store(tmp_path / "state.db") as store:
        engine = SyncEngine(store, api, dry_run=False)
        assert engine.sync([fair()])["errors"]
        assert store.mapping("dry-run", "666") is None
        assert engine.sync([fair()])["actions"][0]["action"] == "noop"
        assert len(api.events) == 1 and store.mapping("dry-run", "666")


def test_real_write_gates_and_synthetic_rejection(tmp_path):
    api = MockCalendarAPI()
    api.is_mock = False
    with Store(tmp_path / "state.db") as store:
        with pytest.raises(CalendarError, match="not explicitly enabled"):
            SyncEngine(store, api, dry_run=False)
        result = SyncEngine(store, api, dry_run=False, allow_real=True).sync([fair()])
        assert result["actions"][0]["action"] == "skip" and not api.calls


def test_unknown_times_cancellation_without_mapping_and_ownership(tmp_path):
    api = MockCalendarAPI()
    with Store(tmp_path / "state.db") as store:
        engine = SyncEngine(store, api, dry_run=False)
        unknown = {**fair(), "end_time": None}
        assert engine.sync([unknown])["actions"][0]["action"] == "skip"
        assert engine.sync([{**fair(), "status": "cancelled"}])["actions"][0]["action"] == "skip"
        api.events["dry-run", event_id("666")] = {"id": event_id("666"), "summary": "他人的事件"}
        assert engine.sync([fair()])["errors"][0]["kind"] == "ownership"
        assert not any(call[0] == "patch" for call in api.calls)


def google(outcomes, **options):
    session = ScriptedSession(outcomes)
    clock = FakeClock()
    credentials = SimpleNamespace(valid=True, token="synthetic-private-token")
    return GoogleCalendarAPI(credentials, session=session, sleep=clock.sleep, monotonic=clock.monotonic, **options), session, clock


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_google_rate_limit_retry_and_no_secrets_in_errors(status):
    api, session, clock = google([response(status=status, headers={"Retry-After": "3"}), response({"id": "event"})])
    assert api.get("calendar@example.com", "event")["id"] == "event"
    assert clock.sleeps == [3] and len(session.sent) == 2
    assert session.sent[0][1]["verify"] is True
    assert session.sent[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("status", [403, 429, 500])
def test_google_exhaustion_does_not_include_response_body_or_token(status):
    api, session, _ = google([response({"secret": "private-response"}, status=status)] * 3)
    with pytest.raises(CalendarError) as caught:
        api.get("calendar", "event")
    assert "private-response" not in str(caught.value) and "synthetic-private-token" not in str(caught.value)
    assert len(session.sent) == (1 if status == 403 else 3)


def test_google_long_retry_after_stops():
    api, session, _ = google([response(status=429, headers={"Retry-After": "120"})])
    with pytest.raises(CalendarError) as caught:
        api.get("calendar", "event")
    assert caught.value.kind == "rate_limit" and len(session.sent) == 1


def test_google_timeout_proxy_and_invalid_json():
    api, _, _ = google([requests.exceptions.ReadTimeout("secret details")] * 3)
    with pytest.raises(CalendarError, match="retries exhausted"):
        api.get("calendar", "event")
    api, session, _ = google([requests.exceptions.ProxyError("403 secret details")])
    with pytest.raises(CalendarError, match="no bypass"):
        api.get("calendar", "event")
    assert len(session.sent) == 1
    api, _, _ = google([response(body=b"<html>private-response</html>")])
    with pytest.raises(CalendarError, match="invalid JSON"):
        api.get("calendar", "event")


def test_missing_credentials_and_private_file_permissions(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_JSON", raising=False)
    with pytest.raises(CalendarError, match="Missing OAuth"):
        GoogleCalendarAPI.from_environment()
    path = tmp_path / "token.json"
    path.write_text('{"refresh_token":"synthetic-only"}')
    path.chmod(0o644)
    with pytest.raises(CalendarError, match="private"):
        GoogleCalendarAPI.from_environment(path)
    path.chmod(0o600)
    with pytest.raises(CalendarError, match="Invalid offline OAuth"):
        GoogleCalendarAPI.from_environment(path)


def test_google_get_not_found_and_delete_gone_are_idempotent():
    api, _, _ = google([response(status=404), response(status=410)])
    assert api.get("calendar", "event") is None
    assert api.cancel("calendar", "event") is None


def test_stable_event_ids_have_google_allowed_alphabet():
    assert event_id("666") == event_id("666")
    assert event_id("666") != event_id("667")
    assert set(event_id("666")) <= set("0123456789abcdefghijklmnopqrstuv")
