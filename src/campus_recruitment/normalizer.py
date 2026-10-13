"""Explicit schema mapping. Synthetic mappings never establish a live API contract."""
from datetime import date, datetime
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

from .errors import ApiError
from .models import Company, Fair, Position, SHANGHAI, now_iso


def clean_text(value, *, max_length=12000):
    if value is None:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = re.sub(r"<[^>]*>", " ", unescape(str(value)))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()
    return text[:max_length] or None


def public_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        return None
    if any(re.search(r"token|password|secret|authorization", key, re.I) for key, _ in parse_qsl(parsed.query)):
        return None
    return value


def parse_time(value) -> tuple[str | None, str | None]:
    """Return explicit datetime and date hint. A date alone is never midnight."""
    if not isinstance(value, str) or not value.strip():
        return None, None
    text = value.strip()
    text = re.sub(r"(\d{4})年(\d{1,2})月(\d{1,2})日", lambda m: f"{m[1]}-{int(m[2]):02}-{int(m[3]):02}", text)
    text = text.replace("时", ":").replace("分", "").replace("：", ":")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None, date.fromisoformat(text).isoformat()
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text):
            return None, None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)
        return parsed.isoformat(timespec="seconds"), parsed.date().isoformat()
    except ValueError:
        return None, None


class Normalizer:
    def __init__(self, config: dict):
        self.config = config
        self.synthetic = config.get("synthetic") is True
        self.verified = config.get("verified") is True
        self.provenance = "synthetic" if self.synthetic else ("school_api" if self.verified else "unverified")
        for kind in ("fair", "company", "position"):
            if not isinstance(config.get("fields", {}).get(kind), dict):
                raise ValueError(f"Missing {kind} field mapping")
        states = config.get("status_values", {})
        if any(value not in {"scheduled", "cancelled", "postponed", "unknown"} for value in states.values()):
            raise ValueError("Unsupported normalized status")

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def get(self, row, kind, name):
        path = self.config["fields"][kind].get(name)
        if path is None:
            return None
        if not isinstance(path, str):
            raise ValueError("Field mappings must be string paths or null")
        value = row
        for key in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    def identifier(self, row, kind, name):
        value = self.get(row, kind, name)
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value)):
            raise ApiError("normalization", f"Missing or invalid {kind}.{name}; stable IDs are required")
        return str(value)

    def fair(self, row, checked_at=None):
        values = {name: clean_text(self.get(row, "fair", name)) for name in
                  ("title", "event_type", "location", "organizer")}
        start, day = parse_time(self.get(row, "fair", "start_time"))
        end, _ = parse_time(self.get(row, "fair", "end_time"))
        announced, announced_day = parse_time(self.get(row, "fair", "announcement_date"))
        raw_status = self.get(row, "fair", "status")
        status = self.config.get("status_values", {}).get(str(raw_status), "unknown")
        issues = []
        if start is None:
            issues.append("开始时间缺失或仅有日期，需人工确认")
        if end is None:
            issues.append("结束时间缺失，禁止推测")
        if start and end and datetime.fromisoformat(end) <= datetime.fromisoformat(start):
            end = None
            issues.append("结束时间不晚于开始时间，需人工确认")
        if status == "unknown" or not self.verified:
            issues.append("活动状态或字段契约未验证")
        return Fair(self.identifier(row, "fair", "job_fair_id"), **values,
                    start_time=start, end_time=end, start_date=day,
                    announcement_date=announced or announced_day,
                    source_url=public_url(self.get(row, "fair", "source_url")),
                    status=status, status_verified=self.verified and status != "unknown",
                    last_checked_at=checked_at or now_iso(), provenance=self.provenance, issues=issues)

    def company(self, row, fair_id, checked_at=None):
        return Company(self.identifier(row, "company", "company_id"),
                       clean_text(self.get(row, "company", "company_name")), [fair_id],
                       checked_at or now_iso(), self.provenance)

    def position(self, row, fair_id, checked_at=None):
        count = self.get(row, "position", "recruitment_count")
        if isinstance(count, str) and re.fullmatch(r"\d+", count):
            count = int(count)
        issues = []
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            if count is not None:
                issues.append("招聘人数格式未知，未推测")
            count = None
        return Position(self.identifier(row, "position", "position_id"),
                        self.identifier(row, "position", "company_id"), fair_id,
                        clean_text(self.get(row, "position", "position_name")),
                        clean_text(self.get(row, "position", "requirements")), count,
                        public_url(self.get(row, "position", "source_url")),
                        last_checked_at=checked_at or now_iso(), provenance=self.provenance, issues=issues)
