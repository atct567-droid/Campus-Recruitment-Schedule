"""RFC 5545 export with UTF-8 octet folding and one event per fair ID."""
import hashlib
from datetime import datetime, timezone

from .normalizer import parse_time
from .reports import public_data


def uid(fair_id):
    digest = hashlib.sha256(str(fair_id).encode()).hexdigest()
    return f"guat-{digest}@campus-recruitment-schedule"


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold(line):
    parts, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " "
        current += char
    parts.append(current)
    return "\r\n".join(parts)


def eligible(fair):
    start, _ = parse_time(fair.get("start_time"))
    end, _ = parse_time(fair.get("end_time"))
    return bool(start and end and datetime.fromisoformat(end) > datetime.fromisoformat(start)
                and fair.get("status_verified") is True and fair.get("status") in {"scheduled", "cancelled"})


def summary(fair, positions):
    selected = sorted([p for p in positions if p["job_fair_id"] == fair["job_fair_id"] and p["matching_score"] > 0],
                      key=lambda p: (-p["matching_score"], p["position_id"]))
    details = ["来源：" + (fair.get("source_url") or "未确认"), "招聘会 ID：" + fair["job_fair_id"],
               "数据类型：" + fair.get("provenance", "unverified")]
    details += [f"{p['position_name']}（{p['matching_score']}分，企业 {p['company_id']}）" for p in selected[:20]]
    return public_data("\n".join(details))


def export_ics(fairs, positions=(), *, reminder_minutes=60, stamp=None):
    if reminder_minutes is not None and (isinstance(reminder_minutes, bool) or not isinstance(reminder_minutes, int) or not 0 <= reminder_minutes <= 10080):
        raise ValueError("Reminder must be 0..10080 minutes or None")
    stamp = stamp or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("ICS stamp must be timezone-aware")
    timestamp = stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Campus Recruitment Schedule//GUAT//ZH", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-TIMEZONE:Asia/Shanghai", "BEGIN:VTIMEZONE", "TZID:Asia/Shanghai",
             "BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:+0800", "TZOFFSETTO:+0800", "TZNAME:CST",
             "END:STANDARD", "END:VTIMEZONE"]
    seen, skipped = {}, []
    for fair in fairs:
        fair = public_data(fair)
        if fair.get("private"):
            raise ValueError("Private activities cannot enter a public calendar")
        identifier = fair["job_fair_id"]
        if identifier in seen:
            if seen[identifier] != fair:
                raise ValueError("Conflicting duplicate fair ID in ICS")
            continue
        seen[identifier] = fair
        if not eligible(fair):
            skipped.append({"job_fair_id": identifier, "reason": "时间不完整或活动状态未确认"})
            continue
        start, _ = parse_time(fair["start_time"])
        end, _ = parse_time(fair["end_time"])
        lines += ["BEGIN:VEVENT", "UID:" + uid(identifier), "DTSTAMP:" + timestamp,
                  "SEQUENCE:" + str(fair.get("revision", 0)),
                  "DTSTART;TZID=Asia/Shanghai:" + datetime.fromisoformat(start).strftime("%Y%m%dT%H%M%S"),
                  "DTEND;TZID=Asia/Shanghai:" + datetime.fromisoformat(end).strftime("%Y%m%dT%H%M%S"),
                  "SUMMARY:" + escape(fair.get("title") or "招聘活动（名称待确认）"),
                  "LOCATION:" + escape(fair.get("location") or "地点待确认"),
                  "DESCRIPTION:" + escape(summary(fair, positions)),
                  "STATUS:" + ("CANCELLED" if fair["status"] == "cancelled" else "CONFIRMED")]
        if fair.get("source_url"):
            lines.append("URL:" + fair["source_url"])
        if reminder_minutes is not None and fair["status"] != "cancelled":
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:招聘活动提醒",
                      f"TRIGGER:-PT{reminder_minutes}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n", skipped
