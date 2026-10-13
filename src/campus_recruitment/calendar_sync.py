"""Persistent idempotent Google Calendar plans; real writes are explicitly gated."""
import base64
import copy
import hashlib
import json
import math
import os
import stat
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests

from .ical import eligible, summary
from .reports import public_data
from .storage import content_hash

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
OWNER = "campus-recruitment-schedule-v1"


class CalendarError(RuntimeError):
    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind, self.status = kind, status


def event_id(fair_id):
    return "guat" + base64.b32hexencode(hashlib.sha256(str(fair_id).encode()).digest()).decode().rstrip("=").lower()


def event_body(fair, positions):
    body = public_data({"summary": fair.get("title") or "招聘活动（名称待确认）",
                        "location": fair.get("location") or "地点待确认",
                        "description": summary(fair, positions),
                        "start": {"dateTime": fair["start_time"], "timeZone": "Asia/Shanghai"},
                        "end": {"dateTime": fair["end_time"], "timeZone": "Asia/Shanghai"},
                        "status": "cancelled" if fair["status"] == "cancelled" else "confirmed",
                        "extendedProperties": {"private": {"owner": OWNER, "fairId": fair["job_fair_id"],
                                                             "provenance": fair["provenance"], "revision": str(fair.get("revision", 0))}}})
    digest = content_hash(body)
    body["extendedProperties"]["private"]["contentHash"] = digest
    return body, digest


class MockCalendarAPI:
    is_mock = True

    def __init__(self):
        self.events = {}
        self.calls = []

    def get(self, calendar_id, identifier):
        self.calls.append(("get", calendar_id, identifier))
        return copy.deepcopy(self.events.get((calendar_id, identifier)))

    def insert(self, calendar_id, identifier, body):
        self.calls.append(("insert", calendar_id, identifier))
        if (calendar_id, identifier) in self.events:
            raise CalendarError("http", "Synthetic conflict", 409)
        self.events[calendar_id, identifier] = {"id": identifier, **copy.deepcopy(body)}
        return copy.deepcopy(self.events[calendar_id, identifier])

    def patch(self, calendar_id, identifier, body):
        self.calls.append(("patch", calendar_id, identifier))
        self.events[calendar_id, identifier] = {"id": identifier, **copy.deepcopy(body)}
        return copy.deepcopy(self.events[calendar_id, identifier])

    def cancel(self, calendar_id, identifier):
        self.calls.append(("cancel", calendar_id, identifier))
        self.events.pop((calendar_id, identifier), None)


class SyncEngine:
    def __init__(self, store, api=None, *, calendar_id="dry-run", dry_run=True, allow_real=False):
        if not dry_run and api is None:
            raise CalendarError("credentials", "Real/mock writes require an API adapter")
        if not dry_run and not api.is_mock and not allow_real:
            raise CalendarError("authorization", "Real calendar writes are not explicitly enabled")
        self.store, self.api = store, api
        self.calendar_id, self.dry_run = calendar_id, dry_run

    @staticmethod
    def _owned(remote, fair):
        properties = remote.get("extendedProperties", {}).get("private", {})
        return properties.get("owner") == OWNER and properties.get("fairId") == fair["job_fair_id"] and properties.get("provenance") == fair["provenance"]

    def sync(self, fairs, positions=()):
        result = {"dry_run": self.dry_run, "real_sync_performed": False, "actions": [], "errors": []}
        seen = set()
        for fair in fairs:
            identifier = fair["job_fair_id"]
            if identifier in seen:
                raise ValueError("Duplicate fair ID in sync input")
            seen.add(identifier)
            mapping = self.store.mapping(self.calendar_id, identifier)
            action = {"job_fair_id": identifier, "event_id": mapping["event_id"] if mapping else event_id(identifier),
                      "provenance": fair["provenance"]}
            if fair.get("private") or not eligible(fair):
                action.update(action="skip", reason="时间或状态不明确，或属于私人记录")
                result["actions"].append(action)
                continue
            if not self.dry_run and not self.api.is_mock and fair["provenance"] != "school_api":
                action.update(action="skip", reason="合成/未验证数据不得写入真实日历")
                result["actions"].append(action)
                continue
            body, digest = event_body(fair, positions)
            cancelled = fair["status"] == "cancelled"
            if cancelled and mapping is None:
                action.update(action="skip", reason="取消活动无已管理事件，不创建取消占位日程")
                result["actions"].append(action)
                continue
            wanted = "cancel" if cancelled else ("update" if mapping else "create")
            if mapping and mapping["content_hash"] == digest:
                wanted = "noop"
            if self.dry_run:
                action.update(action=wanted, content_hash=digest, reason="仅生成计划，不读取授权、不调用 Google、不推进同步状态")
                result["actions"].append(action)
                continue
            try:
                remote = self.api.get(self.calendar_id, action["event_id"])
                # Google may retain a minimal cancelled tombstone without extended properties.
                # An already-managed cancellation needs no remote mutation or ownership override.
                if remote and cancelled and mapping and remote.get("status") == "cancelled":
                    remote = None
                if remote and not self._owned(remote, fair):
                    raise CalendarError("ownership", "Refuse to change a calendar event owned by another source")
                if cancelled:
                    if remote:
                        self.api.cancel(self.calendar_id, action["event_id"])
                    wanted = "cancel" if remote else "noop"
                elif remote:
                    if remote.get("extendedProperties", {}).get("private", {}).get("contentHash") == digest:
                        wanted = "noop"
                    else:
                        self.api.patch(self.calendar_id, action["event_id"], body)
                        wanted = "update"
                else:
                    try:
                        self.api.insert(self.calendar_id, action["event_id"], body)
                    except CalendarError as exc:
                        # A timeout after creation can make the deterministic retry return 409.
                        if exc.status != 409:
                            raise
                        recovered = self.api.get(self.calendar_id, action["event_id"])
                        if not recovered or not self._owned(recovered, fair):
                            raise CalendarError("ownership", "Conflicting event ID is not owned by this application") from None
                        if recovered.get("extendedProperties", {}).get("private", {}).get("contentHash") != digest:
                            self.api.patch(self.calendar_id, action["event_id"], body)
                    wanted = "create"
                self.store.save_mapping(self.calendar_id, identifier, action["event_id"], digest, fair.get("revision", 0), fair["status"])
                action.update(action=wanted, content_hash=digest)
                result["real_sync_performed"] |= not self.api.is_mock and wanted != "noop"
            except CalendarError as exc:
                action.update(action="failed", reason=str(exc))
                result["errors"].append({"job_fair_id": identifier, "kind": exc.kind, "http_status": exc.status, "message": str(exc)})
                result["actions"].append(action)
                break  # Bounded fail-fast; rerun reconciles remaining IDs.
            result["actions"].append(action)
        return result


class GoogleCalendarAPI:
    """Small requests adapter with offline OAuth refresh and bounded retries."""
    is_mock = False

    def __init__(self, credentials, *, session=None, sleep=time.sleep, monotonic=time.monotonic,
                 retries=2, interval=1.0, request_budget=200, max_retry_after=60):
        if retries < 0 or interval < 1 or request_budget < 1:
            raise ValueError("Invalid Google request limits")
        self.credentials = credentials
        self.session = session or requests.Session()
        if session is None:
            self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self.sleep, self.monotonic = sleep, monotonic
        self.retries, self.interval, self.request_budget = retries, interval, request_budget
        self.max_retry_after, self.attempts, self.last = max_retry_after, 0, None

    @classmethod
    def from_environment(cls, token_file=None, **options):
        try:
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise CalendarError("dependency", "Install the calendar extra before enabling OAuth") from None
        if token_file:
            path = Path(token_file)
            if path.is_symlink() or os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise CalendarError("credentials", "OAuth token file must be private (chmod 600), not a symlink")
            text = path.read_text(encoding="utf-8")
        else:
            text = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
        if not text:
            raise CalendarError("credentials", "Missing OAuth credentials; use dry-run or configure a private token file/GitHub Secret")
        try:
            info = json.loads(text)
            if not info.get("refresh_token"):
                raise ValueError
            credentials = Credentials.from_authorized_user_info(info, scopes=SCOPES)
        except (ValueError, TypeError, AttributeError):
            raise CalendarError("credentials", "Invalid offline OAuth credential format; values are never logged") from None
        return cls(credentials, **options)

    def _refresh(self):
        try:
            from google.auth.transport.requests import Request
            self.credentials.refresh(Request(session=self.session))
        except Exception:
            raise CalendarError("credentials", "OAuth refresh failed; verify grants/network privately (credential values omitted)") from None

    def _request(self, method, calendar_id, identifier=None, body=None):
        path = f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
        if identifier:
            path += "/" + quote(identifier, safe="")
        if not self.credentials.valid:
            self._refresh()
        refreshed = False
        for attempt in range(self.retries + 1):
            if self.attempts >= self.request_budget:
                raise CalendarError("request_budget", "Google request budget exhausted")
            if self.last is not None:
                remaining = self.interval - (self.monotonic() - self.last)
                if remaining > 0:
                    self.sleep(remaining)
            self.last = self.monotonic()
            self.attempts += 1
            prepared = requests.Request(method, path, params={"sendUpdates": "none"} if method != "GET" else None,
                                        json=body, headers={"Authorization": "Bearer " + self.credentials.token,
                                                            "Accept": "application/json"}).prepare()
            settings = self.session.merge_environment_settings(prepared.url, {}, None, True, None)
            try:
                response = self.session.send(prepared, timeout=(5, 20), allow_redirects=False, **settings)
            except (requests.exceptions.SSLError, requests.exceptions.ProxyError):
                raise CalendarError("transport_policy", "Google TLS/proxy connection failed; no bypass attempted") from None
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt == self.retries:
                    raise CalendarError("transport", "Google transport retries exhausted (details omitted)") from None
                self.sleep(2 ** (attempt + 1))
                continue
            try:
                status = response.status_code
                if status in (404, 410) and method in ("GET", "DELETE"):
                    return None
                if status == 401 and not refreshed and attempt < self.retries:
                    self._refresh()
                    refreshed = True
                    continue
                if status in (429, 500, 502, 503, 504) and attempt < self.retries:
                    delay = 2 ** (attempt + 1)
                    header = response.headers.get("Retry-After")
                    if header:
                        try:
                            delay = float(header)
                        except ValueError:
                            try:
                                delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
                            except (ValueError, TypeError, OverflowError):
                                raise CalendarError("rate_limit", "Invalid Google Retry-After; stop", status) from None
                    if not math.isfinite(delay) or delay > self.max_retry_after:
                        raise CalendarError("rate_limit", "Google Retry-After exceeds wait limit; retry later", status)
                    self.sleep(max(0, delay))
                    continue
                if not 200 <= status < 300:
                    raise CalendarError("http", "Google API request failed (response/credential details omitted)", status)
                if status == 204 or method == "DELETE":
                    return None
                try:
                    payload = response.json()
                except ValueError:
                    raise CalendarError("schema", "Google returned invalid JSON", status) from None
                if not isinstance(payload, dict):
                    raise CalendarError("schema", "Google event must be an object", status)
                expected_id = identifier or (body or {}).get("id")
                if expected_id is not None and payload.get("id") != expected_id:
                    raise CalendarError("schema", "Google returned a different/missing event ID", status)
                return payload
            finally:
                response.close()
        raise CalendarError("http", "Google retries exhausted")

    def get(self, calendar_id, identifier):
        return self._request("GET", calendar_id, identifier)

    def insert(self, calendar_id, identifier, body):
        return self._request("POST", calendar_id, body={"id": identifier, **body})

    def patch(self, calendar_id, identifier, body):
        return self._request("PATCH", calendar_id, identifier, body)

    def cancel(self, calendar_id, identifier):
        return self._request("DELETE", calendar_id, identifier)

    def close(self):
        self.session.close()


def authorize(client_file, token_file):
    """Run only via the explicit local authorize CLI; never invoked by pipelines."""
    path = Path(token_file)
    if path.exists():
        raise CalendarError("credentials", "Token file already exists; preserve it or explicitly move it before reauthorization")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise CalendarError("dependency", "Install the calendar extra to authorize") from None
    flow = InstalledAppFlow.from_client_secrets_file(client_file, scopes=SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise CalendarError("credentials", "Offline consent did not return a refresh token")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(credentials.to_json())
