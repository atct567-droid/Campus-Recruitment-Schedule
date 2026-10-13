"""Offline-first scheduled sync driver. Secrets remain environment-only."""
import json
import os
from pathlib import Path

from campus_recruitment.cli import main, write_json
from campus_recruitment.storage import Store


def requested_live(environment):
    enabled = environment.get("ENABLE_REAL_CALENDAR_SYNC", "").lower() == "true"
    event = environment.get("CALENDAR_TRIGGER", "manual")
    if event == "schedule":
        return enabled and environment.get("ENABLE_SCHEDULED_CALENDAR_SYNC", "").lower() == "true"
    if event == "manual":
        return enabled and environment.get("CALENDAR_WRITE_REQUESTED", "false").lower() == "true"
    raise ValueError("Only manual or scheduled calendar triggers are supported")


def run(environment=None, *, database=Path("work/calendar.db"), cache=Path("state-cache/real.db"),
        output=Path("reports/calendar")):
    environment = os.environ if environment is None else environment
    live = requested_live(environment)
    summary = {"status": "skipped", "dry_run": not live, "real_sync_performed": False,
               "access_check": {"status": "not_checked"}}
    if not database.exists() and cache.exists():
        with Store(cache) as store:
            store.backup(database)
    if not database.exists():
        summary["reason"] = "没有经过采集的招聘数据库；没有请求 Google 或创建日程"
        write_json(output / "calendar-workflow.json", summary)
        print(summary["reason"])
        return 0
    with Store(database) as store:
        if live:
            runs = store.runs(limit=1)
            if not runs or runs[0]["metadata"].get("errors") or not runs[0]["metadata"].get("complete"):
                summary["reason"] = "最新采集不完整或失败；无人值守写入暂停，旧事件保持原状"
                write_json(output / "calendar-workflow.json", summary)
                print(summary["reason"])
                return 0
        args = ["sync", "--db", str(database), "--output-dir", str(output)]
        if live:
            # This is the same explicit flag + environment gate used by manual CLI.
            args.append("--enable-real-sync")
        elif environment.get("CALENDAR_CHECK_ACCESS", "false").lower() == "true":
            if environment.get("GOOGLE_CREDENTIALS") or environment.get("GOOGLE_OAUTH_TOKEN_JSON"):
                args.append("--check-access")
    try:
        result_path = output / ("calendar-sync.json" if live else "calendar-dry-run.json")
        result_path.unlink(missing_ok=True)  # Do not mistake an earlier run's result for this one.
        code = main(args)
    finally:
        # Retain each successful mapping even if a later Google request fails.
        with Store(database) as store:
            store.backup(cache, public=True, include_calendar_map=True)
    result = json.loads(result_path.read_text()) if result_path.exists() else None
    if result is not None:
        summary.update(real_sync_performed=result["real_sync_performed"], access_check=result["access_check"],
                       actions={name: sum(action["action"] == name for action in result["actions"])
                                for name in ("create", "update", "cancel", "noop", "skip", "failed")})
    if code != 0:
        summary.update(status="failed", reason="日历步骤失败；已保存脱敏结果和成功的映射，未宣称同步成功")
    else:
        summary.update(status="write_run_completed" if live else "dry_run_completed")
    write_json(output / "calendar-workflow.json", summary)
    return code


if __name__ == "__main__":
    raise SystemExit(run())
