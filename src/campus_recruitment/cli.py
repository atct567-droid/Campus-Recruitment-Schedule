"""Offline-first orchestration CLI; live collection and calendar writes are opt-in."""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .calendar_sync import CalendarError, GoogleCalendarAPI, SyncEngine, authorize, validate_calendar_id
from .client import RecruitmentClient
from .collector import QueryPlan, collect
from .fixtures import fixture_client
from .ical import export_ics
from .matcher import JobMatcher
from .models import now_iso
from .normalizer import Normalizer
from .probe import date_argument, today_shanghai
from .reports import generate_reports, public_data
from .schedule import conflicts, fair_activities, load_personal
from .storage import Store

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ROOT = ROOT if (ROOT / "configs").is_dir() else Path(sys.prefix) / "campus-recruitment"


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(public_data(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enabled(variable):
    return os.environ.get(variable, "").strip().lower() == "true"


def private_location(path):
    resolved = Path(path).resolve()
    project_root = ROOT if (ROOT / ".git").exists() else Path.cwd()
    try:
        relative = resolved.relative_to(project_root)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != "private":
        raise ValueError("Private output inside the repository must be under ignored private/")
    return resolved


def run(args):
    output = Path(args.output_dir or Path("reports") / args.mode)
    args.output_dir = output
    output.mkdir(parents=True, exist_ok=True)
    status = {"mode": args.mode, "started_at": now_iso(), "real_school_access_verified": False,
              "real_calendar_sync_performed": False, "status": "not_started"}
    if args.mode == "real" and not enabled("ENABLE_REAL_COLLECTION"):
        status.update(status="skipped", reason="ENABLE_REAL_COLLECTION 未设为 true；未调用学校接口")
        write_json(output / "run.json", status)
        print(status["reason"])
        return 0
    schema_path = args.schema or RESOURCE_ROOT / "configs" / ("synthetic-schema.json" if args.mode == "offline" else "school-schema.example.json")
    normalizer = Normalizer.from_file(schema_path)
    if args.mode == "real" and (normalizer.synthetic or not normalizer.verified):
        status.update(status="blocked", reason="真实字段映射未验证，或配置是 synthetic；未调用学校接口")
        write_json(output / "run.json", status)
        print(status["reason"])
        return 2
    if args.mode == "offline" and not normalizer.synthetic:
        raise ValueError("Offline mode requires a synthetic mapping")
    fixture_path = args.fixture or RESOURCE_ROOT / "fixtures/synthetic-recruitment.json"
    start = args.start
    if start is None:
        start = date.fromisoformat(json.loads(Path(fixture_path).read_text())["reference_date"]) if args.mode == "offline" else today_shanghai()
    if args.days < 1 or args.days > 366:
        raise ValueError("--days must be 1..366")
    end = args.end or start + timedelta(days=args.days)
    query = normalizer.config.get("query", {})
    plan = QueryPlan(start, end, args.strategy or query.get("strategy", "baseline"),
                     query.get("verified") is True, max_queries=args.max_queries)
    plan.dates()  # Reject unsafe/unbounded date ranges before constructing a transport.
    db_path = args.db or Path("state") / f"{args.mode}.db"
    options = {"request_budget": args.request_budget, "max_pages": args.max_pages,
               "max_records": args.max_records, "interval": args.interval, "retries": args.retries,
               "success_codes": normalizer.config.get("success_codes", [200])}
    if args.mode == "real":
        options["public_referer"] = normalizer.config.get("public_referer")
    request_events = []
    options["emit"] = request_events.append
    client = fixture_client(fixture_path, **options) if args.mode == "offline" else RecruitmentClient(**options)
    page_size = min(args.page_size, int(normalizer.config.get("public_page_size", args.page_size))) if args.mode == "real" else args.page_size
    try:
        snapshot = collect(client, normalizer, plan, fair_ids=args.fair_id, page_size=page_size,
                           max_fairs=args.max_fairs, max_records=args.max_records)
    finally:
        client.close()
    matcher = JobMatcher.from_file(args.matching or RESOURCE_ROOT / "configs/matching.json")
    schedule_config = json.loads(Path(args.schedule or RESOURCE_ROOT / "configs/schedule.json").read_text())
    for position in snapshot.positions:
        matcher.match(position)
    with Store(db_path) as store:
        run_id, changes = store.apply(snapshot)
        if not args.no_reports:
            generate_reports(store, output, as_of=start, snapshot=snapshot, priorities=schedule_config.get("priorities"))
        skipped = []
        if not args.no_ics:
            ics, skipped = export_ics(store.list("fair"), store.list("position"),
                                      reminder_minutes=schedule_config.get("reminder_minutes", 60))
            (output / "recruitment.ics").write_bytes(ics.encode("utf-8"))
        if args.calendar_dry_run:
            calendar = SyncEngine(store, calendar_id=os.environ.get("GOOGLE_CALENDAR_ID") or "dry-run").sync(
                store.list("fair"), store.list("position"), store.list("company"))
            write_json(output / "calendar-dry-run.json", calendar)
        if args.personal_file:
            private_output = private_location(args.private_output_dir)
            if private_output.is_relative_to(output.resolve()) or output.resolve().is_relative_to(private_output):
                raise ValueError("Public and private output directories must not overlap")
            personal = load_personal(args.personal_file)
            private_output.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_result = conflicts(fair_activities(store.models("fair")) + personal,
                                       priorities=schedule_config.get("priorities"), include_private=True)
            private_path = private_output / "conflicts.json"
            private_path.write_text(json.dumps(private_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            private_path.chmod(0o600)
        status.update(status="failed" if snapshot.errors else ("complete" if snapshot.complete else "limited"),
                      run_id=run_id, provenance=normalizer.provenance, start=start.isoformat(), end_exclusive=end.isoformat(),
                      collection_complete=snapshot.complete, query_semantics_verified=snapshot.query_verified,
                      requests_attempted=client.attempts, counts={kind: len(store.list(kind)) for kind in ("fair", "company", "position")},
                      changes={kind: sum(c["change_type"] == kind for c in changes) for kind in ("added", "updated", "rescheduled", "cancelled")},
                      errors=snapshot.errors, warnings=snapshot.warnings, ics_skipped=skipped, finished_at=now_iso())
    write_json(output / "requests.jsonl-summary.json", {"mode": args.mode, "requests": request_events})
    write_json(output / "run.json", status)
    print(json.dumps({key: status[key] for key in ("mode", "status", "provenance", "counts", "requests_attempted")}, ensure_ascii=False))
    return 2 if snapshot.errors or not snapshot.complete else 0


def sync(args):
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    live = args.enable_real_sync and enabled("ENABLE_REAL_CALENDAR_SYNC")
    if args.enable_real_sync and not live:
        raise CalendarError("authorization", "Set ENABLE_REAL_CALENDAR_SYNC=true as well as --enable-real-sync")
    configured = os.environ.get("GOOGLE_CALENDAR_ID")
    if configured and args.calendar_id and configured != args.calendar_id:
        raise CalendarError("calendar_scope", "Requested calendar differs from GOOGLE_CALENDAR_ID")
    target = configured or args.calendar_id
    if target:
        validate_calendar_id(target)
    api = None
    with Store(args.db) as store:
        if live or args.check_access:
            if not target:
                raise CalendarError("configuration", "Real sync requires a dedicated calendar ID")
            if any(f["provenance"] != "school_api" for f in store.list("fair")):
                raise CalendarError("provenance", "Synthetic/unverified database cannot be used for real sync")
            api = GoogleCalendarAPI.from_environment(args.token_file, credential_file=args.credential_file, calendar_id=target)
        try:
            result = SyncEngine(store, api, calendar_id=target or "dry-run", dry_run=not live, allow_real=live,
                                read_remote=args.check_access).sync(store.list("fair"), store.list("position"), store.list("company"))
            write_json(output / ("calendar-sync.json" if live else "calendar-dry-run.json"), result)
        finally:
            if api:
                api.close()
    if result["errors"]:
        print("日历步骤失败；请检查脱敏结果，不将失败或模拟运行视为真实同步成功。")
    elif live:
        print("日历执行结果已保存；实际修改以 real_sync_performed 和逐事件结果为准。")
    elif args.check_access:
        print("只读权限预检与 dry-run 已结束；未写入日历或同步映射。")
    else:
        print("离线日历 dry-run 完成；没有读取凭据或调用 Google。")
    return 2 if result["errors"] else 0


def calendar_check(args):
    configured = os.environ.get("GOOGLE_CALENDAR_ID")
    if configured and args.calendar_id and configured != args.calendar_id:
        raise CalendarError("calendar_scope", "Requested calendar differs from GOOGLE_CALENDAR_ID")
    target = validate_calendar_id(configured or args.calendar_id)
    api = GoogleCalendarAPI.from_environment(args.token_file, credential_file=args.credential_file, calendar_id=target)
    try:
        result = api.check_access(target, require_write=True)
        result["real_sync_performed"] = False
        write_json(args.output_dir / "calendar-access.json", result)
    finally:
        api.close()
    print("指定日历只读权限预检已完成；没有创建、修改或取消事件。")
    return 0


def parser():
    root = argparse.ArgumentParser(description="校招管理系统：离线样本、公开报告、ICS、默认日历 dry-run")
    commands = root.add_subparsers(dest="command", required=True)
    job = commands.add_parser("run")
    job.add_argument("--mode", choices=["offline", "real"], default="offline")
    for name in ("fixture", "schema", "db", "matching", "schedule", "personal-file"):
        job.add_argument("--" + name, type=Path)
    job.add_argument("--private-output-dir", type=Path, default=Path("private/diagnostics"))
    job.add_argument("--output-dir", type=Path)
    job.add_argument("--start", type=date_argument)
    job.add_argument("--end", type=date_argument, help="Exclusive window end")
    job.add_argument("--days", type=int, default=30)
    job.add_argument("--fair-id", action="append", default=[])
    job.add_argument("--strategy", choices=["baseline", "daily", "monthly", "start"])
    for name, default in (("max-queries", 40), ("max-pages", 1000), ("max-records", 10000),
                          ("max-fairs", 200), ("request-budget", 200), ("page-size", 50), ("retries", 2)):
        job.add_argument("--" + name, type=int, default=default)
    job.add_argument("--interval", type=float, default=2.0)
    job.add_argument("--no-reports", action="store_true")
    job.add_argument("--no-ics", action="store_true")
    job.add_argument("--calendar-dry-run", action=argparse.BooleanOptionalAction, default=True)
    calendar = commands.add_parser("sync")
    calendar.add_argument("--db", type=Path, required=True)
    calendar.add_argument("--output-dir", type=Path, default=Path("reports/calendar"))
    calendar.add_argument("--calendar-id")
    calendar.add_argument("--token-file", type=Path)
    calendar.add_argument("--credential-file", type=Path, help="Private service-account JSON file; otherwise GOOGLE_CREDENTIALS")
    calendar.add_argument("--check-access", action="store_true", help="Read-only permission and managed-ID inspection; still dry-run")
    calendar.add_argument("--enable-real-sync", action="store_true")
    check = commands.add_parser("calendar-check", help="Read-only permission check for the configured calendar")
    check.add_argument("--calendar-id")
    check.add_argument("--credential-file", type=Path)
    check.add_argument("--token-file", type=Path)
    check.add_argument("--output-dir", type=Path, default=Path("reports/calendar-access"))
    auth = commands.add_parser("authorize")
    auth.add_argument("--client-file", type=Path, required=True)
    auth.add_argument("--token-file", type=Path, required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            return run(args)
        if args.command == "sync":
            return sync(args)
        if args.command == "calendar-check":
            return calendar_check(args)
        authorize(args.client_file, args.token_file)
        print("授权凭据已写入私有文件；不输出 Token。")
        return 0
    except (CalendarError, ValueError, OSError, KeyError) as exc:
        # Config/OAuth JSON may contain secrets; publish only a safe generic summary.
        error = {"status": "failed", "kind": exc.kind if isinstance(exc, CalendarError) else "configuration",
                 "message": str(exc) if isinstance(exc, CalendarError) else "配置、文件或字段异常；请按文档在本地私下检查",
                 "real_sync_performed": False}
        if hasattr(args, "output_dir"):
            write_json(args.output_dir / "failure.json", error)
        print(json.dumps(error, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
