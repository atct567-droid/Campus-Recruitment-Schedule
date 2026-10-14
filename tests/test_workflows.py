from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader preserves GitHub's YAML `on` key instead of YAML 1.1 booleans.
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_cron_matches_three_shanghai_runs_and_live_is_default_off():
    data = workflow("recruitment.yml")
    cron = data["on"]["schedule"][0]["cron"]
    minute, hours, *rest = cron.split()
    assert minute == "0" and rest == ["*", "*", "*"]
    assert {(int(hour) + 8) % 24 for hour in hours.split(",")} == {6, 12, 18}
    assert data["permissions"] == {"contents": "read"}
    assert "'false'" in data["jobs"]["pipeline"]["env"]["ENABLE_REAL_COLLECTION"]
    assert data["on"]["workflow_dispatch"]["inputs"]["enable_real_calendar_sync"]["default"] == "false"
    sync = next(step for step in data["jobs"]["pipeline"]["steps"] if step.get("name", "").startswith("Explicit real"))
    assert "workflow_dispatch" in sync["if"] and "ENABLE_REAL_CALENDAR_SYNC" in sync["if"]


def test_workflow_inputs_not_interpolated_as_shell_and_artifacts_are_scoped():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        data = workflow(path.name)
        for job in data["jobs"].values():
            for step in job["steps"]:
                assert "${{ inputs." not in step.get("run", "")
                if "upload-artifact" in step.get("uses", ""):
                    assert step["if"] == "always()"
                    assert "private" not in step["with"]["path"] and "state" not in step["with"]["path"]


def test_ci_includes_original_tests_lint_pipeline_and_format_validation():
    data = workflow("tests.yml")
    steps = "\n".join(step.get("run", "") for step in data["jobs"]["test"]["steps"])
    assert "pytest" in steps and "ruff" in steps and "campus_recruitment.cli" in steps and "validate_outputs" in steps
    assert data["jobs"]["test"]["strategy"]["matrix"]["python-version"] == ["3.10", "3.12"]
