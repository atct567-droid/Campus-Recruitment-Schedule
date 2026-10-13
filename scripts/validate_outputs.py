"""Independent JSON/CSV/ICS audit used by CI and local delivery validation."""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from icalendar import Calendar

from campus_recruitment.ical import eligible, uid
from campus_recruitment.models import Company, Fair, Position


def validate(directory):
    folder = Path(directory)
    run = json.loads((folder / "run.json").read_text())
    if run["status"] in {"skipped", "blocked"}:
        if not run.get("reason") or run["real_school_access_verified"] is not False:
            raise ValueError("Skipped/blocked runs must state a reason and no verified school access")
        return {"status": run["status"], "reason": run["reason"]}
    data = json.loads((folder / "recruitment.json").read_text())
    if data["timezone"] != "Asia/Shanghai" or data["schema_version"] != 1:
        raise ValueError("Unknown export schema or timezone")
    classes = {"fairs": Fair, "companies": Company, "positions": Position}
    counts = {}
    for kind, cls in classes.items():
        seen = set()
        for row in data[kind]:
            if set(row) != set(cls.__dataclass_fields__) | {"first_seen_at", "revision"}:
                raise ValueError(f"Unexpected fields in {kind}")
            key = (row["job_fair_id"], row["company_id"], row["position_id"]) if kind == "positions" else row["job_fair_id" if kind == "fairs" else "company_id"]
            if key in seen:
                raise ValueError(f"Duplicate {kind} ID")
            seen.add(key)
            for name in ("start_time", "end_time", "last_checked_at", "first_seen_at"):
                if row.get(name):
                    stamp = datetime.fromisoformat(row[name])
                    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 8 * 3600:
                        raise ValueError("Times must use explicit Asia/Shanghai +08:00 offset")
            if row["provenance"] not in {"synthetic", "school_api", "unverified"}:
                raise ValueError("Missing provenance")
            if kind == "positions" and not 0 <= row["matching_score"] <= 100:
                raise ValueError("Invalid matching score")
        with (folder / f"{kind}.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if set(reader.fieldnames) != set(cls.__dataclass_fields__) | {"first_seen_at", "revision"}:
                raise ValueError("CSV schema mismatch")
            rows = list(reader)
            if len(rows) != len(data[kind]):
                raise ValueError("CSV/JSON row counts differ")
            if any(str(value).lstrip().startswith(("=", "+", "-", "@")) for row in rows for value in row.values()):
                raise ValueError("Unsafe spreadsheet formula")
        counts[kind] = len(data[kind])
    raw = (folder / "recruitment.ics").read_bytes()
    if b"\r\n" not in raw or any(len(line) > 75 for line in raw.split(b"\r\n")):
        raise ValueError("ICS CRLF/folding invalid")
    events = Calendar.from_ical(raw).walk("VEVENT")
    expected = {uid(f["job_fair_id"]) for f in data["fairs"] if eligible(f)}
    identifiers = [str(event["UID"]) for event in events]
    if set(identifiers) != expected or len(set(identifiers)) != len(identifiers):
        raise ValueError("ICS event IDs differ from eligible public fairs")
    if any(event.decoded("DTEND") <= event.decoded("DTSTART") for event in events):
        raise ValueError("ICS interval invalid")
    return {"status": "validated", **counts, "ics_events": len(events), "provenance": data["provenance"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.directory), ensure_ascii=False))
