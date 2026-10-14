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


def eligible(fair, *, allow_observed=False):
    start, _ = parse_time(fair.get("start_time"))
    end, _ = parse_time(fair.get("end_time"))
    trusted_status = fair.get("status_verified") is True and fair.get("status") in {"scheduled", "cancelled"}
    observed = allow_observed and fair.get("provenance") == "school_api" and fair.get("status") == "unknown"
    return bool(start and end and datetime.fromisoformat(end) > datetime.fromisoformat(start)
                and (trusted_status or observed))


def summary(fair, positions, companies=(), *, include_all_positions=False):
    selected = sorted([p for p in positions if p["job_fair_id"] == fair["job_fair_id"]
                       and (include_all_positions or p["matching_score"] > 0)],
                      key=lambda p: (-p["matching_score"], p["position_id"]))
    related = sorted([company for company in companies if fair["job_fair_id"] in company.get("related_job_fair_ids", [])],
                     key=lambda company: company["company_id"])
    names = {company["company_id"]: company.get("company_name") or "企业名称未确认" for company in companies}
    details = ["官网链接：", fair.get("source_url") or "未确认", "", "招聘会 ID：" + fair["job_fair_id"],
               "地点：" + (fair.get("location") or "未确认"),
               "数据类型：" + fair.get("provenance", "unverified")]
    details += ["参会企业：" + ("；".join(f"{c.get('company_name') or '名称未确认'}（{c['company_id']}）" for c in related[:50]) or "未确认")]
    if len(related) > 50:
        details.append(f"企业摘要列出 50 家，共 {len(related)} 家；完整列表见招聘报告。")
    details.extend(fair.get("calendar_notes", []))
    details.append("公开岗位（匹配分数优先）：" if include_all_positions else "匹配岗位：")
    details += [f"{p['position_name']}（{p['matching_score']}分，{names.get(p['company_id'], '企业名称未确认')} / {p['company_id']}）" for p in selected[:20]] or ["暂无已确认匹配岗位"]
    return public_data("\n".join(details))


def export_ics(fairs, positions=(), *, reminder_minutes=60, stamp=None, companies=(),
               reminder_offsets=(), allow_observed=False, include_all_positions=False, calendar_name=None):
    if reminder_minutes is not None and (isinstance(reminder_minutes, bool) or not isinstance(reminder_minutes, int) or not 0 <= reminder_minutes <= 10080):
        raise ValueError("Reminder must be 0..10080 minutes or None")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10080
           for value in reminder_offsets):
        raise ValueError("Reminder offsets must be integers from 0 to 10080 minutes")
    reminders = list(dict.fromkeys(([reminder_minutes] if reminder_minutes is not None else []) + list(reminder_offsets)))
    stamp = stamp or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("ICS stamp must be timezone-aware")
    timestamp = stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Campus Recruitment Schedule//GUAT//ZH", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-TIMEZONE:Asia/Shanghai"]
    if calendar_name:
        lines.extend(["X-WR-CALNAME:" + escape(calendar_name), "X-PUBLISHED-TTL:PT6H", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"])
    lines.extend(["BEGIN:VTIMEZONE", "TZID:Asia/Shanghai", "BEGIN:STANDARD", "DTSTART:19700101T000000",
                  "TZOFFSETFROM:+0800", "TZOFFSETTO:+0800", "TZNAME:CST", "END:STANDARD", "END:VTIMEZONE"])
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
        if not eligible(fair, allow_observed=allow_observed):
            skipped.append({"job_fair_id": identifier, "reason": "时间不完整或活动状态未确认"})
            continue
        start, _ = parse_time(fair["start_time"])
        end, _ = parse_time(fair["end_time"])
        modified = fair.get("calendar_updated_at")
        modified_stamp = datetime.fromisoformat(modified).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if modified else timestamp
        lines += ["BEGIN:VEVENT", "UID:" + uid(identifier), "DTSTAMP:" + modified_stamp,
                  "LAST-MODIFIED:" + modified_stamp,
                  "SEQUENCE:" + str(fair.get("revision", 0)),
                  "DTSTART;TZID=Asia/Shanghai:" + datetime.fromisoformat(start).strftime("%Y%m%dT%H%M%S"),
                  "DTEND;TZID=Asia/Shanghai:" + datetime.fromisoformat(end).strftime("%Y%m%dT%H%M%S"),
                  "SUMMARY:" + escape(fair.get("title") or "招聘活动（名称待确认）"),
                  "LOCATION:" + escape(fair.get("location") or "地点待确认"),
                  "DESCRIPTION:" + escape(fair.get("calendar_description") or summary(fair, positions, companies, include_all_positions=include_all_positions)),
                  "STATUS:" + ({"cancelled": "CANCELLED", "scheduled": "CONFIRMED"}.get(fair["status"], "TENTATIVE"))]
        if fair.get("source_url"):
            lines.append("URL:" + fair["source_url"])
        if fair["status"] != "cancelled":
            for reminder in reminders:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:招聘活动提醒",
                          f"TRIGGER:-PT{reminder}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n", skipped
