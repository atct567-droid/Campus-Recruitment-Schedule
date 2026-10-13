import json
import runpy
import subprocess
import sys
from pathlib import Path

from campus_recruitment.cli import main
from campus_recruitment.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def test_cli_end_to_end_without_sockets_credentials_and_idempotency(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network adapter must not be constructed")
    monkeypatch.setattr("campus_recruitment.cli.RecruitmentClient", forbidden)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_JSON", raising=False)
    db, output = tmp_path / "state.db", tmp_path / "public"
    args = ["run", "--db", str(db), "--output-dir", str(output)]
    assert main(args) == 0
    first = json.loads((output / "run.json").read_text())
    assert first["provenance"] == "synthetic" and first["requests_attempted"] == 22
    assert main(args) == 0
    second = json.loads((output / "run.json").read_text())
    assert second["changes"] == {"added": 0, "updated": 0, "rescheduled": 0, "cancelled": 0}
    assert first["counts"] == second["counts"] == {"fair": 5, "company": 3, "position": 7}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_outputs.py"), str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ics_events"] == 4
    assert main(["sync", "--db", str(db), "--output-dir", str(output)]) == 0
    assert json.loads((output / "calendar-dry-run.json").read_text())["real_sync_performed"] is False


def test_default_real_collection_switch_off_is_safe_skip(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_REAL_COLLECTION", raising=False)
    monkeypatch.setattr("campus_recruitment.cli.RecruitmentClient", lambda **kw: (_ for _ in ()).throw(AssertionError("network invoked")))
    output = tmp_path / "public"
    assert main(["run", "--mode", "real", "--db", str(tmp_path / "state.db"), "--output-dir", str(output)]) == 0
    report = json.loads((output / "run.json").read_text())
    assert report["status"] == "skipped" and "ENABLE_REAL_COLLECTION" in report["reason"]
    assert not (tmp_path / "state.db").exists()


def test_real_mapping_guard_and_calendar_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_COLLECTION", "true")
    output = tmp_path / "public"
    assert main(["run", "--mode", "real", "--output-dir", str(output)]) == 2
    assert json.loads((output / "run.json").read_text())["status"] == "blocked"
    monkeypatch.delenv("ENABLE_REAL_CALENDAR_SYNC", raising=False)
    assert main(["sync", "--db", str(tmp_path / "state.db"), "--enable-real-sync", "--output-dir", str(output)]) == 2
    assert "authorization" in (output / "failure.json").read_text()


def test_private_conflicts_never_appear_in_public_outputs(tmp_path):
    personal = tmp_path / "private-input.json"
    personal.write_text(json.dumps({"visibility": "private", "activities": [{"id": "personal-1", "title": "绝不能公开的面试内容", "kind": "interview", "start": "2026-10-15 10:00", "end": "2026-10-15 11:00"}]}))
    public, private = tmp_path / "public", tmp_path / "private-output"
    assert main(["run", "--db", str(tmp_path / "state.db"), "--output-dir", str(public), "--personal-file", str(personal), "--private-output-dir", str(private)]) == 0
    assert "绝不能公开" in (private / "conflicts.json").read_text()
    for path in public.iterdir():
        assert "绝不能公开" not in path.read_text()
    with Store(tmp_path / "state.db") as store:
        assert all("绝不能公开" not in json.dumps(row, ensure_ascii=False) for row in store.list("fair"))


def test_workflow_argument_injection_is_literal_and_rejected_as_id(tmp_path):
    functions = runpy.run_path(str(ROOT / "scripts/run_workflow.py"))
    attack = "666; touch /tmp/should-not-exist"
    args = functions["arguments"]({"RUN_MODE": "offline", "RUN_FAIR_ID": attack})
    assert attack in args
    assert main(["run", "--fair-id", attack, "--db", str(tmp_path / "db"), "--output-dir", str(tmp_path / "out")]) == 2


def test_public_state_backup_removes_calendar_identity(tmp_path):
    with Store(tmp_path / "private.db") as store:
        store.save_mapping("private-calendar@example.com", "666", "event", "digest", 0, "scheduled")
        store.backup(tmp_path / "public.db", public=True)
    with Store(tmp_path / "public.db") as store:
        assert store.mapping("private-calendar@example.com", "666") is None
