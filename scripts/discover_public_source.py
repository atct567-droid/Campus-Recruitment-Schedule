"""Inspect same-origin public frontend assets; no authenticated API calls."""

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from campus_recruitment.client import BASE_URL, RecruitmentClient
from campus_recruitment.models import now_iso


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("src"):
            self.sources.append(attributes["src"])


def main():
    destination = Path("reports/public-discovery")
    destination.mkdir(parents=True, exist_ok=True)
    report = {"started_at": now_iso(), "anonymous": True, "calendar_writes": False,
              "requests": [], "frontend_observations": [], "recruitment_records_collected": 0}
    client = RecruitmentClient(interval=2, request_budget=16, retries=0)

    def get_public(path):
        url = urljoin(BASE_URL + "/", path)
        if urlsplit(url).netloc != urlsplit(BASE_URL).netloc:
            raise ValueError("Only same-origin public resources are accepted")
        client._limit()
        request = requests.Request("GET", url, headers={
            "User-Agent": "Campus-Recruitment-Schedule/0.2 (public recruitment source verification)"
        }).prepare()
        settings = client.session.merge_environment_settings(request.url, {}, None, True, None)
        response = client.session.send(request, timeout=client.timeout, allow_redirects=False, **settings)
        try:
            entry = {"url": url, "http_status": response.status_code,
                     "content_type": response.headers.get("Content-Type"),
                     "sha256": hashlib.sha256(response.content).hexdigest(), "byte_count": len(response.content)}
            report["requests"].append(entry)
            if response.status_code in (401, 403, 429):
                raise ValueError("Access or rate limit encountered; stopping public inspection")
            if response.status_code != 200:
                return ""
            if len(response.content) > 4000000:
                entry["not_parsed"] = "Public asset exceeds inspection size limit"
                return ""
            return response.content.decode("utf-8", errors="replace")
        finally:
            response.close()

    try:
        homepage = get_public("/index")
        parser = Scripts()
        parser.feed(homepage)
        sources = [path for path in parser.sources if path.startswith("/static/js/app~")]
        for name in ("index", "jobfairSearch", "jobfairDetail", "positionSearch"):
            matches = re.findall(r'"(' + name + r'~[^"\s]+)":"([a-f0-9]{8})"', homepage)
            if matches:
                chunk, digest = matches[0]
                sources.append(f"/static/js/{chunk}.{digest}.js")
        report["public_script_paths"] = list(dict.fromkeys(sources))
        terms = ("baseURL", "jobFair/list", "getJobFair", "position/companyGroupList",
                 "jobFair/positionList", "calendarStartDate", "isToken", "schoolCode", "schoolId")
        for path in report["public_script_paths"][:12]:
            source = get_public(path)
            snippets = []
            for term in terms:
                for match in list(re.finditer(re.escape(term), source))[:3]:
                    snippets.append({"term": term, "snippet": source[max(0, match.start()-260):match.end()+420]})
            if snippets:
                report["frontend_observations"].append({"source": urljoin(BASE_URL, path), "snippets": snippets})
    except (requests.RequestException, ValueError) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        client.close()
        report["requests_attempted"] = client.attempts
        report["finished_at"] = now_iso()
        (destination / "discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("BEGIN_PUBLIC_SOURCE_DISCOVERY_JSON")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("END_PUBLIC_SOURCE_DISCOVERY_JSON")
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
