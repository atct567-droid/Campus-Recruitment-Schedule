"""Synthetic HTTP transport: exercises the production parser/paginator without sockets."""
import copy
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from .client import COMPANY_PATH, LIST_PATH, POSITION_PATH, RecruitmentClient


class FixtureSession(requests.Session):
    def __init__(self, fixture):
        super().__init__()
        if fixture.get("provenance") != "synthetic":
            raise ValueError("Offline fixture must explicitly declare synthetic provenance")
        self.fixture = copy.deepcopy(fixture)
        self.sent = []
        self.faults = self.fixture.get("faults", [])

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        return {"proxies": {}, "stream": False, "verify": True, "cert": None}

    def send(self, request, **settings):
        parsed = urlsplit(request.url)
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.sent.append({"path": parsed.path, "params": params})
        for fault in self.faults:
            if fault.get("remaining", 1) > 0 and fault.get("path") == parsed.path and all(params.get(k) == str(v) for k, v in fault.get("params", {}).items()):
                fault["remaining"] = fault.get("remaining", 1) - 1
                if fault.get("exception") == "timeout":
                    raise requests.exceptions.ReadTimeout("synthetic timeout")
                if fault.get("exception") == "proxy403":
                    raise requests.exceptions.ProxyError("synthetic: Tunnel connection failed: 403 Forbidden")
                return self._response(fault.get("payload", {"code": 500}), fault.get("status", 200), fault.get("headers"))
        if parsed.path == LIST_PATH:
            rows = self.fixture["fairs"]
            query = params.get("calendarStartDate")
            if query and self.fixture.get("date_semantics") == "day_equals":
                rows = [row for row in rows if str(row.get("start", ""))[:10] == query]
        elif parsed.path.startswith("/api/view/getJobFair/"):
            identifier = parsed.path.rsplit("/", 1)[1]
            detail = self.fixture["details"].get(identifier)
            return self._response({"code": 200, "data": detail})
        elif parsed.path in (COMPANY_PATH, POSITION_PATH):
            key = "companies" if parsed.path == COMPANY_PATH else "positions"
            rows = self.fixture[key].get(params.get("jobFairId"), [])
        else:
            return self._response({"code": 404}, 404)
        size = min(int(params.get("pageSize", "50")), self.fixture.get("page_cap", 100))
        number = int(params.get("pageNum", "1"))
        selected = rows[(number - 1) * size:number * size]
        total_page = math.ceil(len(rows) / size)
        layout = self.fixture.get("layouts", {}).get(parsed.path, "rows")
        pagination = {"total": len(rows), "totalPage": total_page}
        if layout == "data.rows":
            payload = {"code": 200, "data": {"rows": selected, **pagination}}
        elif layout == "data":
            payload = {"code": 200, "data": selected, **pagination}
        else:
            payload = {"code": 200, "rows": selected, **pagination}
        return self._response(payload)

    @staticmethod
    def _response(payload, status=200, headers=None):
        result = requests.Response()
        result.status_code = status
        result.headers.update({"Content-Type": "application/json", **(headers or {})})
        result._content = json.dumps(payload).encode()
        result._content_consumed = True
        return result


def fixture_client(path=None, *, fixture=None, **options):
    data = fixture if fixture is not None else json.loads(Path(path).read_text(encoding="utf-8"))
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    return RecruitmentClient(session=FixtureSession(data), sleep=sleep, monotonic=lambda: clock[0], **options)
