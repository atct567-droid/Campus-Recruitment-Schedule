"""Known public bounds must not trigger requests to protected later pages."""

import json

from campus_recruitment.client import COMPANY_PATH, RecruitmentClient
from scripts import resume_public_preview


def test_public_page_bound_keeps_partial_records_and_never_requests_page_six(monkeypatch):
    pages = []

    def fake_get(self, path, params):
        pages.append(params["pageNum"])
        assert params["pageNum"] <= 5
        return {"code": 200, "total": 60, "totalPage": 6,
                "rows": [{"companyId": params["pageNum"] * 10 + n, "companyName": "Mock company"} for n in range(10)]}

    monkeypatch.setattr(RecruitmentClient, "get_json", fake_get)
    client = resume_public_preview.BoundedPublicClient()
    try:
        rows, complete, last_page = client.visible(COMPANY_PATH, "991")
    finally:
        client.close()
    assert pages == [1, 2, 3, 4, 5]
    assert len(rows) == 50 and complete is False and last_page.total == 60


def test_resume_default_does_not_read_school_or_require_input_files(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_REAL_COLLECTION", raising=False)
    monkeypatch.setattr(resume_public_preview.preview, "OUTPUT", tmp_path)
    monkeypatch.setattr(resume_public_preview, "BoundedPublicClient", lambda **kw: (_ for _ in ()).throw(AssertionError("network invoked")))
    assert resume_public_preview.main() == 2
    assert json.loads((tmp_path / "disabled.json").read_text())["status"] == "skipped"
