"""Translate Actions inputs to an argument list, never to executable shell text."""
import json
import os
from pathlib import Path

from campus_recruitment.cli import main
from campus_recruitment.storage import Store


def arguments(environment):
    mode = environment.get("RUN_MODE", "real")
    if mode not in {"offline", "real"}:
        raise ValueError("Unsupported workflow mode")
    args = ["run", "--mode", mode, "--db", "work/run.db", "--output-dir", "reports/public"]
    for variable, flag in (("RUN_START", "--start"), ("RUN_END", "--end"), ("RUN_FAIR_ID", "--fair-id"), ("RUN_SCHEMA", "--schema")):
        value = environment.get(variable, "")
        if value:
            args.extend([flag, value])
    if environment.get("RUN_REPORTS", "true") == "false":
        args.append("--no-reports")
    if environment.get("RUN_CALENDAR_DRY_RUN", "true") == "false":
        args.append("--no-calendar-dry-run")
    return args


def run():
    mode = os.environ.get("RUN_MODE", "real")
    cached = Path("state-cache") / f"{mode}.db"
    if cached.exists():
        with Store(cached) as store:
            store.backup("work/run.db")
    code = main(arguments(os.environ))
    result = Path("reports/public/run.json")
    outcome = json.loads(result.read_text()) if result.exists() else {"status": "failed"}
    if Path("work/run.db").exists() and outcome["status"] not in {"skipped", "blocked"}:
        with Store("work/run.db") as store:
            store.backup(cached, public=True, include_calendar_map=True)
    return code


if __name__ == "__main__":
    raise SystemExit(run())
