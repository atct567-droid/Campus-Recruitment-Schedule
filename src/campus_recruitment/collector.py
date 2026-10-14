"""Date-query adapter and bounded collection using the original API client."""
from dataclasses import dataclass
from datetime import date, timedelta

from .errors import ApiError
from .models import Snapshot, now_iso


@dataclass(frozen=True)
class QueryPlan:
    start: date
    end: date  # Exclusive local-date boundary.
    strategy: str = "baseline"
    verified: bool = False
    max_queries: int = 40

    def dates(self):
        if self.end <= self.start:
            raise ValueError("Window end must be after start (exclusive end)")
        if self.strategy == "baseline":
            result = [None]
        elif self.strategy == "start":
            result = [self.start.isoformat()]
        elif self.strategy == "daily":
            result = [(self.start + timedelta(days=n)).isoformat() for n in range((self.end - self.start).days)]
        elif self.strategy == "monthly":
            result = []
            current = self.start.replace(day=1)
            while current < self.end:
                result.append(current.isoformat())
                current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
                if len(result) > self.max_queries:
                    raise ValueError("Date-query budget exceeded")
        else:
            raise ValueError("Unsupported date-query strategy")
        if len(result) > self.max_queries:
            raise ValueError("Date-query budget exceeded")
        return result


def collect(client, normalizer, plan: QueryPlan, *, fair_ids=(), page_size=50, max_fairs=200, max_records=10000):
    snapshot = Snapshot(mode="offline" if normalizer.synthetic else "real", query_verified=plan.verified,
                        checked_at=now_iso())
    if not plan.verified:
        snapshot.warnings.append("日期查询语义未验证：仅按本地窗口筛选已观察记录，覆盖范围不完整；不推断取消")
    ids = dict.fromkeys(client._fair_id(value) for value in fair_ids)
    summaries = {}
    try:
        if not fair_ids:
            for query in plan.dates():
                result = client.list_fairs(query, page_size=page_size)
                for row in result.rows:
                    identifier = normalizer.identifier(row, "fair", "job_fair_id")
                    if plan.strategy == "daily" and plan.verified:
                        observed = normalizer.fair(row, snapshot.checked_at).start_date
                        if observed != query:
                            raise ApiError("query_semantics", "Daily query returned a different or missing start date; coverage is incomplete")
                    if identifier in summaries and summaries[identifier] != row:
                        snapshot.warnings.append(f"招聘会 {identifier} 重复摘要，使用详情核对")
                    summaries[identifier] = row
                    ids[identifier] = None
                    if len(ids) > max_fairs:
                        raise ApiError("record_limit", "Fair limit exceeded; snapshot is incomplete")
        if len(ids) > max_fairs:
            raise ApiError("record_limit", "Fair limit exceeded")
        for identifier in ids:
            detail = client.fair_detail(identifier)
            if normalizer.identifier(detail, "fair", "job_fair_id") != identifier:
                raise ApiError("normalization", "Detail ID differs from requested fair ID")
            fair = normalizer.fair(detail, snapshot.checked_at)
            # Unknown dates remain available for manual review; known out-of-window fairs are not collected.
            if not fair_ids and fair.start_date and not (plan.start <= date.fromisoformat(fair.start_date) < plan.end):
                continue
            companies = [normalizer.company(row, identifier, snapshot.checked_at)
                         for row in client.companies(identifier, page_size=page_size).rows]
            positions = [normalizer.position(row, identifier, snapshot.checked_at)
                         for row in client.positions(identifier, page_size=page_size).rows]
            known_companies = {company.company_id for company in companies}
            missing_companies = {position.company_id for position in positions} - known_companies
            if missing_companies:
                snapshot.warnings.append(f"招聘会 {identifier} 的岗位企业 ID 缺少企业记录：" + ",".join(sorted(missing_companies)))
            if len(snapshot.positions) + len(snapshot.companies) + len(positions) + len(companies) > max_records:
                raise ApiError("record_limit", "Snapshot record limit exceeded")
            # Commit a fair to the snapshot only after all its subrequests succeed.
            snapshot.fairs.append(fair)
            snapshot.companies.extend(companies)
            snapshot.positions.extend(positions)
        snapshot.complete = plan.verified
    except (ApiError, ValueError) as exc:
        error = exc.as_dict() if isinstance(exc, ApiError) else {"kind": "configuration", "message": str(exc), "http_status": None}
        snapshot.errors.append(error)
    return snapshot
