"""Half-open overlap analysis; private activities never enter public recruitment models."""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .normalizer import parse_time

DEFAULT_PRIORITIES = {"interview": 100, "written_test": 90, "important_talk": 80, "job_fair": 60, "talk": 40}


@dataclass(frozen=True)
class Activity:
    identifier: str
    title: str
    kind: str
    start: str | None
    end: str | None
    location: str | None = None
    private: bool = False
    cancelled: bool = False


def interval(activity):
    if not activity.start or not activity.end or activity.cancelled:
        return None
    start, _ = parse_time(activity.start)
    end, _ = parse_time(activity.end)
    if not start or not end:
        return None
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return (a, b) if b > a else None


def fair_activities(fairs):
    return [Activity(f.job_fair_id, f.title or "名称待确认", f.event_type or "unknown", f.start_time, f.end_time,
                     f.location, cancelled=f.status == "cancelled" and f.status_verified) for f in fairs]


def load_personal(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("visibility") != "private":
        raise ValueError("Personal schedule must declare visibility=private")
    activities = []
    for row in payload["activities"]:
        activities.append(Activity(str(row["id"]), row["title"], row["kind"], row.get("start"), row.get("end"),
                                   row.get("location"), private=True, cancelled=row.get("cancelled") is True))
    return activities


def conflicts(activities, *, priorities=None, include_private=False):
    weights = {**DEFAULT_PRIORITIES, **(priorities or {})}
    selected = [a for a in activities if include_private or not a.private]
    ordered = sorted(((interval(a), a) for a in selected if interval(a)), key=lambda item: (item[0][0], item[1].identifier))
    result = []
    for index, (left_interval, left) in enumerate(ordered):
        for right_interval, right in ordered[index + 1:]:
            if right_interval[0] >= left_interval[1]:
                break
            if left_interval[0] < right_interval[1] and right_interval[0] < left_interval[1]:
                preferred = max((left, right), key=lambda a: (weights.get(a.kind, 0), a.identifier))
                result.append({"left_id": left.identifier, "left_title": left.title,
                               "right_id": right.identifier, "right_title": right.title,
                               "overlap_start": max(left_interval[0], right_interval[0]).isoformat(),
                               "overlap_end": min(left_interval[1], right_interval[1]).isoformat(),
                               "suggested_priority_id": preferred.identifier,
                               "private": left.private or right.private,
                               "action": "仅供参考，不自动取消活动"})
    return result


def daily_order(activities, *, priorities=None, include_private=False):
    weights = {**DEFAULT_PRIORITIES, **(priorities or {})}
    selected = [a for a in activities if (include_private or not a.private) and not a.cancelled]
    return sorted(selected, key=lambda a: (a.start or "9999", -weights.get(a.kind, 0), a.location or "", a.identifier))
