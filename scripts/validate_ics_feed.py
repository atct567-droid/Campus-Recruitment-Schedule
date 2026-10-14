"""Independent subscription-file validation through the icalendar parser."""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from icalendar import Calendar

from campus_recruitment.ical import uid


def validate(folder):
    folder = Path(folder)
    raw = (folder / "recruitment.ics").read_bytes()
    if not raw.endswith(b"\r\n") or any(len(line) > 75 for line in raw.split(b"\r\n")):
        raise ValueError("Invalid RFC5545 line endings or UTF-8 folding")
    events = Calendar.from_ical(raw).walk("VEVENT")
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    by_uid = {uid(identifier): item for identifier, item in state["events"].items()}
    identifiers = [str(event["UID"]) for event in events]
    if set(identifiers) != set(by_uid) or len(set(identifiers)) != len(events) or len(events) != metadata["events"]:
        raise ValueError("Event mapping/count mismatch")
    for event in events:
        item = by_uid[str(event["UID"])]
        fair = item["fair"]
        start, end = event.decoded("DTSTART"), event.decoded("DTEND")
        if end <= start or start.utcoffset() != timedelta(hours=8) or end.utcoffset() != timedelta(hours=8):
            raise ValueError("Invalid Shanghai event interval")
        if start != datetime.fromisoformat(fair["start_time"]) or end != datetime.fromisoformat(fair["end_time"]):
            raise ValueError("Source/calendar time mismatch")
        if int(event["SEQUENCE"]) != item["revision"]:
            raise ValueError("Event revision mismatch")
        if str(event["DESCRIPTION"]) != fair["calendar_description"]:
            raise ValueError("Event description mismatch")
        if str(event.get("URL", "")) != (fair.get("source_url") or ""):
            raise ValueError("Event source URL mismatch")
        status = str(event["STATUS"])
        if fair["status"] == "unknown" and status != "TENTATIVE":
            raise ValueError("Unknown source status cannot be confirmed/cancelled")
        if status == "CANCELLED" and (fair["status"] != "cancelled" or fair["status_verified"] is not True):
            raise ValueError("Cancellation lacks verified source evidence")
        alarms = [component.decoded("TRIGGER") for component in event.subcomponents if component.name == "VALARM"]
        if status != "CANCELLED" and sorted(alarms) != [timedelta(days=-1), timedelta(minutes=-30)]:
            raise ValueError("Expected one-day and 30-minute reminders")
    return {"validation": "passed", "events": len(events), "provenance": metadata["provenance"],
            "collection_complete": metadata["collection_complete"], "real_account_calendar_write": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.folder), ensure_ascii=False))
