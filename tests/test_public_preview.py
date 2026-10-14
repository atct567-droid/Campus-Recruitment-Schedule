"""Offline fault injection verifies that partial public pages never claim success."""

import importlib.util
import json
from pathlib import Path

import pytest

from campus_recruitment.client import COMPANY_PATH, POSITION_PATH, RecruitmentClient
from campus_recruitment.errors import ApiError
from campus_recruitment.models import Snapshot
from campus_recruitment.normalizer import Normalizer

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("fail_second_page", [False, True])
def test_preview_preserves_observed_rows_and_truthful_completeness(tmp_path, monkeypatch, fail_second_page):
    spec = importlib.util.spec_from_file_location("public_preview_test", ROOT / "scripts/collect_public_preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "OUTPUT", tmp_path / "reports")
    module.OUTPUT.mkdir()
    fair = {"jobFairId": 991, "jobFairName": "Mock fair", "startTime": "2026-10-15 09:00:00",
            "endTime": "2026-10-15 12:00:00", "jobFairAddress": "Mock location", "state": 2,
            "companyCount": 1, "positionCount": 1}
    (module.OUTPUT / "listing.json").write_text(json.dumps({"listing_complete": True, "fairs": [fair]}))

    def fake_get(self, path, params=None):
        if path.startswith("/api/view/getJobFair/"):
            return {"code": 200, "data": fair}
        assert params["pageSize"] == 10
        if path == COMPANY_PATH:
            return {"code": 200, "rows": [{"companyId": 10, "companyName": "Mock company"}], "total": 1, "totalPage": 1}
        assert path == POSITION_PATH
        if params["pageNum"] == 2:
            raise ApiError("access_denied", "Mock denied second page", status=403)
        return {"code": 200, "rows": [{"positionId": 11, "companyId": 10, "companyName": "Mock company",
                 "positionName": "嵌入式工程师", "positionDesc": "STM32研发", "personsNum": 2}],
                "total": 2 if fail_second_page else 1, "totalPage": 2 if fail_second_page else 1}

    monkeypatch.setattr(RecruitmentClient, "get_json", fake_get)
    schema = Normalizer.from_file(ROOT / "configs/school-public-schema.json")
    assert module.details(schema) == (2 if fail_second_page else 0)
    public = json.loads((module.OUTPUT / "public-records.json").read_text())
    assert public["collection_complete"] is (not fail_second_page)
    assert public["real_calendar_sync_performed"] is False
    assert len(public["companies"]) == len(public["positions"]) == 1
    assert public["positions"][0]["fair_subcollection_complete"] is (not fail_second_page)
    assert public["positions"][0]["matching_priority"] == "high"
    normalized = json.loads((module.OUTPUT / "recruitment.json").read_text())
    assert len(normalized["fairs"]) == (0 if fail_second_page else 1)
    if not fail_second_page:
        assert normalized["fairs"][0]["status"] == "unknown"
        assert normalized["fairs"][0]["source_url"] == "https://bysjyw.guat.edu.cn/m/pages/jobfair/jobfair-detail?jobFairId=991"


def test_real_cli_uses_public_page_size_budget_and_truthful_access_flag_with_mock(tmp_path, monkeypatch):
    from campus_recruitment import cli
    observed = []

    class MockClient:
        attempts = 1

        def __init__(self, **options):
            observed.append(options)
            options["emit"]({"outcome": "json_verified"})

        def close(self):
            pass

    def empty_mock_collection(client, normalizer, plan, **options):
        assert options["page_size"] == 10
        return Snapshot(mode="real", complete=True, query_verified=True)

    monkeypatch.setenv("ENABLE_REAL_COLLECTION", "true")
    monkeypatch.setattr(cli, "RecruitmentClient", MockClient)
    monkeypatch.setattr(cli, "collect", empty_mock_collection)
    args = ["run", "--mode", "real", "--schema", str(ROOT / "configs/school-public-schema.json"),
            "--db", str(tmp_path / "mock.db"), "--output-dir", str(tmp_path / "reports")]
    assert cli.main(args) == 0
    assert observed[-1]["request_budget"] == 420
    assert json.loads((tmp_path / "reports/run.json").read_text())["real_school_access_verified"] is True
    assert cli.main(args + ["--request-budget", "42"]) == 0
    assert observed[-1]["request_budget"] == 42
