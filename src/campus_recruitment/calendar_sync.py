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
from .storage import calendar_scope, content_hash

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
OWNER = "campus-recruitment-schedule-v1"
DEFAULT_REMINDERS = (1440, 30)
WRITE_ROLES = {"writer", "writerWithoutPrivateAccess", "owner"}


def validate_calendar_id(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 1024 or any(ord(c) < 33 for c in value):
        raise CalendarError("configuration", "A nonempty explicit GOOGLE_CALENDAR_ID is required")
    if value == "primary":
        raise CalendarError("calendar_scope", "The primary alias is not allowed; use the explicitly shared calendar ID")
    return value


class CalendarError(RuntimeError):
    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind, self.status = kind, status


def event_id(fair_id):
    return "guat" + base64.b32hexencode(hashlib.sha256(str(fair_id).encode()).digest()).decode().rstrip("=").lower()


def event_body(fair, positions, companies=(), *, reminder_minutes=DEFAULT_REMINDERS):
    reminders = sorted(set(reminder_minutes), reverse=True)
    if len(reminders) > 5 or any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 40320 for n in reminders):
        raise ValueError("Google reminders must contain at most 5 offsets of 0..40320 minutes")
    body = public_data({"summary": fair.get("title") or "招聘活动（名称待确认）",
                        "location": fair.get("location") or "地点待确认",
                        "description": summary(fair, positions, companies),
                        "start": {"dateTime": fair["start_time"], "timeZone": "Asia/Shanghai"},
                        "end": {"dateTime": fair["end_time"], "timeZone": "Asia/Shanghai"},
                        "status": "cancelled" if fair["status"] == "cancelled" else "confirmed",
                        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": n} for n in reminders]},
                        "extendedProperties": {"private": {"owner": OWNER, "fairId": fair["job_fair_id"],
                                                             "provenance": fair["provenance"], "revision": str(fair.get("revision", 0))}}})
    digest = content_hash(body)
    body["extendedProperties"]["private"]["contentHash"] = digest
    return body, digest


def same_event(remote, desired):
    """Detect drift even when a manual edit leaves our content-hash marker intact."""
    if any(remote.get(key) != desired.get(key) for key in ("summary", "location", "description", "status")):
        return False
    for key in ("start", "end"):
        try:
            a, b = remote[key], desired[key]
            if datetime.fromisoformat(a["dateTime"].replace("Z", "+00:00")) != datetime.fromisoformat(b["dateTime"]):
                return False
            if a.get("timeZone") != b["timeZone"]:
                return False
        except (KeyError, TypeError, ValueError):
            return False
    observed = remote.get("reminders", {})
    expected = desired["reminders"]
    if observed.get("useDefault") is not False:
        return False
    if sorted((r.get("method"), r.get("minutes")) for r in observed.get("overrides", [])) != sorted((r["method"], r["minutes"]) for r in expected["overrides"]):
        return False
    return remote.get("extendedProperties", {}).get("private", {}).get("contentHash") == desired["extendedProperties"]["private"]["contentHash"]


class MockCalendarAPI:
    is_mock = True

    def __init__(self, *, access_role="writer"):
        self.events = {}
        self.calls = []
        self.access_role = access_role

    def check_access(self, calendar_id, *, require_write=True):
        self.calls.append(("check_access", calendar_id, ""))
        if require_write and self.access_role not in WRITE_ROLES:
            raise CalendarError("calendar_permission", "指定日历没有可写权限；请检查共享权限")
        return {"status": "verified_mock", "access_role": self.access_role, "writable": self.access_role in WRITE_ROLES,
                "calendar_time_zone": "Asia/Shanghai", "calendar_scope": calendar_scope(calendar_id)}

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
    def __init__(self, store, api=None, *, calendar_id="dry-run", dry_run=True, allow_real=False,
                 read_remote=False, reminder_minutes=DEFAULT_REMINDERS):
        if not dry_run and api is None:
            raise CalendarError("credentials", "Real/mock writes require an API adapter")
        if not dry_run and not api.is_mock and not allow_real:
            raise CalendarError("authorization", "Real calendar writes are not explicitly enabled")
        self.store, self.api = store, api
        self.calendar_id, self.dry_run = calendar_id, dry_run
        self.read_remote, self.reminder_minutes = read_remote, reminder_minutes

    @staticmethod
    def _owned(remote, fair):
        properties = remote.get("extendedProperties", {}).get("private", {})
        return properties.get("owner") == OWNER and properties.get("fairId") == fair["job_fair_id"] and properties.get("provenance") == fair["provenance"]

    def sync(self, fairs, positions=(), companies=()):
        result = {"dry_run": self.dry_run, "real_sync_performed": False, "actions": [], "errors": [],
                  "calendar_scope": calendar_scope(self.calendar_id),
                  "access_check": {"status": "not_checked", "reason": "离线计划未读取凭据或验证真实权限"}}
        check_remote = self.api is not None and (not self.dry_run or self.read_remote)
        allowed = [f for f in fairs if not f.get("private") and eligible(f) and
                   (self.dry_run or self.api.is_mock or f["provenance"] == "school_api")]
        if check_remote and allowed:
            try:
                result["access_check"] = self.api.check_access(self.calendar_id, require_write=True)
            except CalendarError as exc:
                result["access_check"] = {"status": "failed", "kind": exc.kind, "http_status": exc.status}
                result["errors"].append({"kind": exc.kind, "http_status": exc.status, "message": str(exc)})
                return result
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
            body, digest = event_body(fair, positions, companies, reminder_minutes=self.reminder_minutes)
            cancelled = fair["status"] == "cancelled"
            if cancelled and mapping is None and not check_remote:
                action.update(action="skip", reason="取消活动无已管理事件，不创建取消占位日程")
                result["actions"].append(action)
                continue
            wanted = "cancel" if cancelled else ("update" if mapping else "create")
            if mapping and mapping["content_hash"] == digest:
                wanted = "noop"
            if self.dry_run and not check_remote:
                action.update(action=wanted, content_hash=digest, event_preview=body,
                              reason="仅生成计划，不读取授权、不调用 Google、不推进同步状态")
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
                if self.dry_run:
                    wanted = ("cancel" if remote else "skip") if cancelled else ("noop" if remote and same_event(remote, body) else "update" if remote else "create")
                    action.update(action=wanted, content_hash=digest, event_preview=body,
                                  reason="权限已只读预检；仅读取指定 ID，不写事件或推进映射")
                    result["actions"].append(action)
                    continue
                if cancelled:
                    if remote:
                        self.api.cancel(self.calendar_id, action["event_id"])
                    wanted = "cancel" if remote else ("noop" if mapping else "skip")
                elif remote:
                    if same_event(remote, body):
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
                        if not same_event(recovered, body):
                            self.api.patch(self.calendar_id, action["event_id"], body)
                    wanted = "create"
                if wanted != "skip":
                    self.store.save_mapping(self.calendar_id, identifier, action["event_id"], digest, fair.get("revision", 0), fair["status"])
                action.update(action=wanted, content_hash=digest)
                result["real_sync_performed"] |= not self.api.is_mock and wanted in {"create", "update", "cancel"}
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
                 retries=2, interval=1.0, request_budget=200, max_retry_after=60,
                 calendar_id=None, auth_kind="oauth"):
        if retries < 0 or interval < 1 or request_budget < 1:
            raise ValueError("Invalid Google request limits")
        self.credentials = credentials
        self.calendar_id = validate_calendar_id(calendar_id) if calendar_id is not None else None
        self.auth_kind = auth_kind
        self.session = session or requests.Session()
        if session is None:
            self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self.sleep, self.monotonic = sleep, monotonic
        self.retries, self.interval, self.request_budget = retries, interval, request_budget
        self.max_retry_after, self.attempts, self.last = max_retry_after, 0, None

    @classmethod
    def from_environment(cls, token_file=None, *, credential_file=None, calendar_id=None, **options):
        # An explicit OAuth token file remains supported. Otherwise prefer GOOGLE_CREDENTIALS.
        source = credential_file or token_file
        if credential_file and token_file:
            raise CalendarError("configuration", "Choose a service credential file or an OAuth token file")
        if source:
            path = Path(source)
            if path.is_symlink() or os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise CalendarError("credentials", "Credential file must be private (chmod 600), not a symlink")
            text = path.read_text(encoding="utf-8")
        else:
            text = os.environ.get("GOOGLE_CREDENTIALS") or os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
        if not text:
            raise CalendarError("credentials", "Missing OAuth/service-account credentials; use dry-run or configure GOOGLE_CREDENTIALS privately")
        try:
            if len(text) > 65536:
                raise ValueError
            info = json.loads(text)
            if not isinstance(info, dict):
                raise ValueError
            service_account = info.get("type") == "service_account" and token_file is None
            if service_account:
                from google.oauth2.service_account import Credentials
                if info.get("token_uri") != "https://oauth2.googleapis.com/token":
                    raise ValueError
                credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
            else:
                from google.oauth2.credentials import Credentials
                if not info.get("refresh_token") or credential_file:
                    raise ValueError
                credentials = Credentials.from_authorized_user_info(info, scopes=SCOPES)
        except ImportError:
            raise CalendarError("dependency", "Install the calendar extra before loading credentials") from None
        except Exception:
            # Signer/JSON exceptions can contain private key material; never propagate them.
            raise CalendarError("credentials", "Invalid offline OAuth/service-account credential format; values are never logged") from None
        configured = os.environ.get("GOOGLE_CALENDAR_ID")
        if configured and calendar_id and configured != calendar_id:
            raise CalendarError("calendar_scope", "Requested calendar differs from configured GOOGLE_CALENDAR_ID")
        target = validate_calendar_id(configured or calendar_id)
        return cls(credentials, calendar_id=target, auth_kind="service_account" if service_account else "oauth", **options)

    def _bind(self, calendar_id):
        target = validate_calendar_id(calendar_id)
        if self.calendar_id is None:
            self.calendar_id = target
        if target != self.calendar_id:
            raise CalendarError("calendar_scope", "This adapter can access only its configured GOOGLE_CALENDAR_ID")
        return target

    def check_access(self, calendar_id, *, require_write=True):
        # Events.list metadata provides accessRole even for a shared calendar that
        # is not subscribed in the service account's CalendarList. No items requested.
        payload = self._request("GET", calendar_id, query={"maxResults": 1, "fields": "accessRole,timeZone"})
        role = payload.get("accessRole") if payload else None
        if role not in {"reader", "freeBusyReader", *WRITE_ROLES}:
            raise CalendarError("calendar_permission", "Calendar access role is missing or inaccessible")
        if require_write and role not in WRITE_ROLES:
            raise CalendarError("calendar_permission", "指定日历没有可写权限；请向服务账号共享“更改活动”权限")
        return {"status": "verified", "calendar_scope": calendar_scope(self.calendar_id), "access_role": role,
                "writable": role in WRITE_ROLES, "calendar_time_zone": payload.get("timeZone"),
                "event_time_zone": "Asia/Shanghai", "auth_kind": self.auth_kind}

    def _refresh(self):
        try:
            from google.auth.transport.requests import Request
            self.credentials.refresh(Request(session=self.session))
        except Exception:
            raise CalendarError("credentials", "OAuth refresh failed; verify grants/network privately (credential values omitted)") from None

    def _request(self, method, calendar_id, identifier=None, body=None, *, query=None):
        calendar_id = self._bind(calendar_id)
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
            prepared = requests.Request(method, path, params=query if method == "GET" else {"sendUpdates": "none"},
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
