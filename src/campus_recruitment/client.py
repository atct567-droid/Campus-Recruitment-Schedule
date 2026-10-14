import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

import requests

from .errors import ApiError
from .parsing import parse_detail, parse_page

BASE_URL = "https://bysjyw.guat.edu.cn"
LIST_PATH = "/api/view/jobFair/list"
POSITION_PATH = "/api/view/jobFair/positionList"
COMPANY_PATH = "/api/view/position/companyGroupList"


def safe_message(exc: Exception) -> str:
    # Exception strings may contain a proxy URL. Never expose URI credentials.
    return re.sub(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[redacted]@", str(exc))[:600]


@dataclass(frozen=True)
class Collection:
    rows: list[dict]
    pages_fetched: int
    total: int | None
    total_page: int | None
    layouts: list[str]

    def as_dict(self) -> dict:
        return {"rows": self.rows, "pages_fetched": self.pages_fetched,
                "total": self.total, "totalPage": self.total_page, "layouts": self.layouts}


class RecruitmentClient:
    """Sequential anonymous GETs; retain environment proxy and TLS verification."""

    def __init__(self, *, base_url=BASE_URL, success_codes=(200,), interval=2.0,
                 timeout=(5.0, 20.0), retries=2, max_pages=1000, request_budget=200,
                 max_retry_after=60.0, session=None, sleep=time.sleep,
                 monotonic=time.monotonic, emit=None, max_records=10000,
                 public_referer=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("base_url must be an HTTPS origin without credentials")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an origin without path, query, or fragment")
        if interval < 1 or len(timeout) != 2 or any(t <= 0 or not math.isfinite(t) for t in timeout):
            raise ValueError("Use interval >= 1 second and positive finite connect/read timeouts")
        if not math.isfinite(interval) or retries < 0 or max_pages < 1 or request_budget < 1:
            raise ValueError("Invalid request limits")
        if not math.isfinite(max_retry_after) or max_retry_after < 0:
            raise ValueError("Invalid Retry-After limit")
        self.base_url = base_url.rstrip("/")
        if public_referer is not None:
            referer = urlsplit(public_referer)
            if (referer.scheme != "https" or referer.netloc != parsed.netloc
                    or referer.username or referer.password or referer.query or referer.fragment
                    or referer.path not in {"/index", "/jobfairSearch", "/positionSearch"}):
                raise ValueError("public_referer must be a same-origin public recruitment page")
        self.public_referer = public_referer
        self.success_codes = {str(code) for code in success_codes}
        if not self.success_codes:
            raise ValueError("At least one explicitly accepted business code is required")
        self.interval, self.timeout, self.retries = interval, timeout, retries
        self.max_pages, self.request_budget = max_pages, request_budget
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self.max_records = max_records
        self.max_retry_after = max_retry_after
        self.session = session or requests.Session()
        # Avoid adapter retries outside our limiter/budget.
        if session is None:
            self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self.sleep, self.monotonic, self.emit = sleep, monotonic, emit or (lambda _: None)
        self.attempts = 0
        self._last_start = None

    def close(self):
        self.session.close()

    def _limit(self):
        if self.attempts >= self.request_budget:
            raise ApiError("request_budget", "Request budget exhausted; result is incomplete")
        if self._last_start is not None:
            remaining = self.interval - (self.monotonic() - self._last_start)
            if remaining > 0:
                self.sleep(remaining)
        self._last_start = self.monotonic()
        self.attempts += 1

    def _retry_delay(self, response, attempt):
        delay = min(2 ** (attempt + 1), self.max_retry_after)
        value = response.headers.get("Retry-After") if response is not None else None
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    raise ApiError("retry_after", "Invalid Retry-After; stop and retry later", status=response.status_code)
            if not math.isfinite(delay) or delay > self.max_retry_after:
                raise ApiError("retry_after", "Retry-After exceeds wait limit; stop and retry later", status=response.status_code)
        return max(0.0, delay)

    def get_json(self, path: str, params: dict | None = None) -> dict:
        if not path.startswith("/api/view/") or "?" in path or "#" in path:
            raise ValueError("Only API paths are accepted")
        params = dict(params or {})
        url = self.base_url + path
        for attempt in range(self.retries + 1):
            self._limit()
            started = self.monotonic()
            entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "path": path,
                     "params": params, "attempt": attempt + 1, "http_status": None}
            response = None
            try:
                # Request.prepare avoids Session/netrc auth and Session cookies.
                headers = {
                    "Accept": "application/json",
                    "User-Agent": "Campus-Recruitment-Schedule/0.1 (anonymous API validation)",
                }
                if self.public_referer:
                    headers["Referer"] = self.public_referer
                prepared = requests.Request("GET", url, params=params, headers=headers).prepare()
                settings = self.session.merge_environment_settings(prepared.url, {}, None, True, None)
                response = self.session.send(prepared, timeout=self.timeout, allow_redirects=False, **settings)
            except requests.exceptions.RequestException as exc:
                message = safe_message(exc)
                denied = isinstance(exc, requests.exceptions.ProxyError) and bool(
                    re.search(r"\b(403|407)\b|denied|forbidden|not allowed", message, re.I))
                if denied:
                    kind = "proxy_policy"
                elif isinstance(exc, requests.exceptions.SSLError):
                    kind = "tls"
                elif isinstance(exc, requests.exceptions.Timeout):
                    kind = "timeout"
                elif isinstance(exc, requests.exceptions.ConnectionError):
                    kind = "connection"
                else:
                    kind = "transport"
                retry = kind in ("timeout", "connection") and attempt < self.retries
                entry.update(error=kind, message=message, retry=retry,
                             elapsed_ms=round((self.monotonic() - started) * 1000, 2))
                self.emit(entry)
                if not retry:
                    raise ApiError(kind, message) from exc
                self.sleep(self._retry_delay(None, attempt))
                continue

            try:
                status = response.status_code
                entry.update(http_status=status, elapsed_ms=round((self.monotonic() - started) * 1000, 2),
                             content_type=response.headers.get("Content-Type"))
                if status in (401, 403, 407):
                    raise ApiError("access_denied", "Access denied; no authentication or bypass attempted", status=status)
                if 300 <= status < 400:
                    raise ApiError("redirect", "Redirect refused; target may require login", status=status)
                if status == 429 or status in (500, 502, 503, 504):
                    retry = attempt < self.retries
                    entry.update(error="http", retry=retry)
                    if retry:
                        delay = self._retry_delay(response, attempt)
                        entry["retry_delay_seconds"] = delay
                        self.emit(entry)
                        self.sleep(delay)
                        continue
                    raise ApiError("http", "Retryable HTTP failure exhausted retries", status=status)
                if not 200 <= status < 300:
                    raise ApiError("http", "Unexpected HTTP status", status=status)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ApiError("invalid_json", "HTTP response is not valid JSON", status=status) from exc
                if not isinstance(payload, dict):
                    raise ApiError("schema", "JSON root must be an object", status=status)
                code = payload.get("code")
                entry["business_code"] = code if isinstance(code, (str, int)) else None
                if isinstance(code, bool) or not isinstance(code, (str, int)) or str(code) not in self.success_codes:
                    raise ApiError("business", "Missing or unaccepted business code", status=status)
                entry["outcome"] = "json_verified"
                self.emit(entry)
                return payload
            except ApiError as exc:
                entry.update(error=exc.kind, message=str(exc), retry=False)
                self.emit(entry)
                raise
            finally:
                response.close()
        raise AssertionError("unreachable")

    def fetch_all(self, path: str, *, params=None, page_size=50) -> Collection:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise ValueError("page_size must be an integer between 1 and 100")
        params = dict(params or {})
        if "pageNum" in params or "pageSize" in params:
            raise ValueError("Pagination parameters are controlled by fetch_all")
        rows, seen, layouts = [], set(), set()
        expected = None
        for number in range(1, self.max_pages + 1):
            page = parse_page(self.get_json(path, {**params, "pageNum": number, "pageSize": page_size}))
            metadata = (page.total, page.total_page)
            if expected is None:
                expected = metadata
            elif metadata != expected:
                raise ApiError("pagination", "Pagination metadata changed; result is incomplete")
            layouts.add(page.layout)
            if not page.rows:
                missing_rows = page.total is not None and len(rows) != page.total
                empty_advertised_page = (page.total_page is not None and page.total_page > 0
                                         and number <= page.total_page
                                         and not (number == 1 and page.total in (None, 0) and page.total_page == 1))
                if missing_rows or empty_advertised_page:
                    raise ApiError("pagination", "Empty page before advertised end; result is incomplete")
                return Collection(rows, number, page.total, page.total_page, sorted(layouts))
            signatures = [hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                          for row in page.rows]
            if len(set(signatures)) != len(signatures) or any(signature in seen for signature in signatures):
                raise ApiError("pagination", "Repeated rows; page parameter may be ignored or data changed")
            seen.update(signatures)
            rows.extend(page.rows)
            if len(rows) > self.max_records or page.total is not None and page.total > self.max_records:
                raise ApiError("record_limit", "Record limit exceeded; result is incomplete")
            if page.total is not None and len(rows) > page.total:
                raise ApiError("pagination", "Rows exceed advertised total")
            end_by_total = page.total is not None and len(rows) == page.total
            end_by_pages = page.total_page is not None and number == page.total_page
            if end_by_total or end_by_pages:
                if page.total is not None and not end_by_total or page.total_page is not None and not end_by_pages:
                    raise ApiError("pagination", "total and totalPage disagree; result is incomplete")
                return Collection(rows, number, page.total, page.total_page, sorted(layouts))
        raise ApiError("pagination", "Page limit reached; result is incomplete")

    def list_fairs(self, calendar_start_date: str | None = None, *, page_size=50):
        params = {} if calendar_start_date is None else {"calendarStartDate": calendar_start_date}
        if calendar_start_date is not None:
            try:
                if datetime.strptime(calendar_start_date, "%Y-%m-%d").strftime("%Y-%m-%d") != calendar_start_date:
                    raise ValueError
            except ValueError:
                raise ValueError("calendarStartDate must be YYYY-MM-DD") from None
        return self.fetch_all(LIST_PATH, params=params, page_size=page_size)

    @staticmethod
    def _fair_id(value) -> str:
        text = str(value)
        if isinstance(value, bool) or value is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text):
            raise ValueError("jobFairId must be a nonempty alphanumeric ID")
        return text

    def fair_detail(self, fair_id):
        return parse_detail(self.get_json(f"/api/view/getJobFair/{quote(self._fair_id(fair_id), safe='')}"))

    def positions(self, fair_id, *, page_size=50):
        return self.fetch_all(POSITION_PATH, params={"jobFairId": self._fair_id(fair_id)}, page_size=page_size)

    def companies(self, fair_id, *, page_size=50):
        return self.fetch_all(COMPANY_PATH, params={"jobFairId": self._fair_id(fair_id)}, page_size=page_size)
