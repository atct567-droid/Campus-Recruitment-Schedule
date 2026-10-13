"""Normalized public recruitment models; no raw responses or private activities."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


@dataclass
class Fair:
    job_fair_id: str
    title: str | None = None
    event_type: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    organizer: str | None = None
    source_url: str | None = None
    announcement_date: str | None = None
    status: str = "unknown"
    last_checked_at: str = ""
    start_date: str | None = None
    status_verified: bool = False
    provenance: str = "unverified"
    issues: list[str] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


@dataclass
class Company:
    company_id: str
    company_name: str | None = None
    related_job_fair_ids: list[str] = field(default_factory=list)
    last_checked_at: str = ""
    provenance: str = "unverified"

    def as_dict(self):
        return asdict(self)


@dataclass
class Position:
    position_id: str
    company_id: str
    job_fair_id: str
    position_name: str | None = None
    requirements: str | None = None
    recruitment_count: int | None = None
    source_url: str | None = None
    matching_score: int = 0
    matching_priority: str = "low"
    matching_reasons: list[str] = field(default_factory=list)
    last_checked_at: str = ""
    provenance: str = "unverified"
    issues: list[str] = field(default_factory=list)

    @property
    def key(self):
        return [self.job_fair_id, self.company_id, self.position_id]

    def as_dict(self):
        return asdict(self)


@dataclass
class Snapshot:
    fairs: list[Fair] = field(default_factory=list)
    companies: list[Company] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "offline"
    complete: bool = False
    query_verified: bool = False
    checked_at: str = ""
