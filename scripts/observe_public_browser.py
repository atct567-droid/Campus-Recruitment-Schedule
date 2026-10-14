"""Observe normal public pages in a fresh browser; no credentials or login."""

import asyncio
import json
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import async_playwright

from campus_recruitment.client import BASE_URL
from campus_recruitment.models import now_iso


def redact(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if re.search(r"token|secret|password|authorization|cookie|private.key", key, re.I)
                else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def safe_url(url):
    parsed = urlsplit(url)
    query = urlencode([(key, "[redacted]" if re.search(r"token|secret|password|authorization|key", key, re.I) else value)
                       for key, value in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


async def main():
    output = Path("reports/public-browser")
    output.mkdir(parents=True, exist_ok=True)
    report = {"started_at": now_iso(), "fresh_context": True, "login_performed": False,
              "calendar_writes": False, "api_responses": [], "pages": []}
    pending = set()
    origin = urlsplit(BASE_URL).netloc

    async def observe(response):
        parsed = urlsplit(response.url)
        if parsed.netloc != origin or not parsed.path.startswith("/api/"):
            return
        request = response.request
        headers = await request.all_headers()
        entry = {"url": safe_url(response.url), "method": request.method, "http_status": response.status,
                 "request_headers": {key: value for key, value in headers.items()
                                     if not re.search(r"token|authorization|cookie|secret|key", key, re.I)},
                 "sensitive_headers_present": [key for key in headers if re.search(r"token|authorization|cookie|secret|key", key, re.I)]}
        try:
            entry["response_json"] = redact(await response.json())
        except Exception:
            entry["response_format"] = "not JSON or body unavailable"
        report["api_responses"].append(entry)

    def on_response(response):
        task = asyncio.create_task(observe(response))
        pending.add(task)
        task.add_done_callback(pending.discard)

    try:
        executable = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=executable)
            context = await browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
            page = await context.new_page()
            page.on("response", on_response)
            for path in ("/index", "/jobfairSearch"):
                try:
                    await page.goto(BASE_URL + path, wait_until="networkidle", timeout=45000)
                    await page.wait_for_timeout(5000)
                    report["pages"].append({"requested_path": path, "final_url": page.url,
                                            "title": await page.title(), "visible_text": (await page.locator("body").inner_text())[:25000]})
                except Exception as error:
                    report["pages"].append({"requested_path": path, "error": type(error).__name__ + ": " + str(error)[:300]})
                await page.wait_for_timeout(2000)
            if pending:
                await asyncio.gather(*list(pending), return_exceptions=True)
            await context.close()
            await browser.close()
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    report["finished_at"] = now_iso()
    (output / "browser.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("BEGIN_PUBLIC_BROWSER_OBSERVATION_JSON")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("END_PUBLIC_BROWSER_OBSERVATION_JSON")
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
