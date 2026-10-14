"""Build public school ICS from an actual snapshot or bounded anonymous collection."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from campus_recruitment.client import RecruitmentClient
from campus_recruitment.matcher import JobMatcher
from campus_recruitment.models import SHANGHAI, now_iso
from campus_recruitment.normalizer import Normalizer
from campus_recruitment.subscription import build_feed, collect_public_snapshot, normalize_snapshot

ROOT = Path(__file__).resolve().parents[1]


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="Actual public school snapshot JSON")
    source.add_argument("--collect", action="store_true", help="Explicit bounded anonymous school collection")
    parser.add_argument("--listing", type=Path, help="Reviewed complete date listing for a snapshot")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    normalizer = Normalizer.from_file(ROOT / "configs/school-public-schema.json")
    diagnostics = []
    if args.collect:
        if os.environ.get("ENABLE_REAL_COLLECTION") != "true":
            raise ValueError("Real collection requires ENABLE_REAL_COLLECTION=true")
        events = []
        client = RecruitmentClient(interval=2, request_budget=420, max_pages=120, emit=events.append,
                                   public_referer=normalizer.config["public_referer"])
        try:
            snapshot, diagnostics = collect_public_snapshot(
                client, normalizer, JobMatcher.from_file(ROOT / "configs/matching.json"),
                start=datetime.now(SHANGHAI).date())
        finally:
            client.close()
        diagnostics = {"requests_attempted": client.attempts, "requests": events, "partial_details": diagnostics}
    else:
        records = json.loads(args.snapshot.read_text(encoding="utf-8"))
        listing = json.loads(args.listing.read_text(encoding="utf-8")) if args.listing else None
        snapshot = normalize_snapshot(records, normalizer, listing=listing)
    state_file = args.output / "state.json"
    previous = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
    calendar, state, metadata = build_feed(snapshot, previous, published_at=now_iso())
    # All validation above precedes replacement; failed collection preserves the published branch.
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "recruitment.ics").write_bytes(calendar.encode("utf-8"))
    save_json(state_file, state)
    save_json(args.output / "metadata.json", metadata)
    save_json(args.output / "collection-log.json", diagnostics)
    (args.output / ".nojekyll").touch()
    (args.output / "README.md").write_text(
        "# 桂航校招 ICS 订阅\n\n"
        "订阅地址：https://raw.githubusercontent.com/atct567-droid/Campus-Recruitment-Schedule/ics-feed/recruitment.ics\n\n"
        "仅包含学校公开招聘活动，不需要 Google/Apple 凭据。"
        "状态未核实的活动标为暂定；企业、岗位不完整时在说明中标注。"
        "采集时间与完整性见 metadata.json，刷新由订阅客户端决定。\n",
        encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
