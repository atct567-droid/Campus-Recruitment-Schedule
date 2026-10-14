"""Read-only public-link diagnosis in a normal GitHub Actions runner."""

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from playwright.async_api import async_playwright

from campus_recruitment.models import now_iso
from campus_recruitment.reports import public_data

ORIGIN = "https://bysjyw.guat.edu.cn"
OUTPUT = Path("reports/school-links")
OUTPUT.mkdir(parents=True, exist_ok=True)
report = {"checked_at": now_iso(), "login_performed": False, "account_writes": False,
          "http": [], "route_snippets": [], "browser": []}


def save():
    (OUTPUT / "result.json").write_text(json.dumps(public_data(report), ensure_ascii=False, indent=2) + "\n")


def read(session, url):
    time.sleep(2)
    request = requests.Request("GET", url, headers={"User-Agent": "Campus-Recruitment-Schedule/link-check"}).prepare()
    response = session.send(request, timeout=(5, 20), allow_redirects=False,
                            **session.merge_environment_settings(url, {}, None, True, None))
    report["http"].append({"url": url, "http_status": response.status_code,
                           "content_type": response.headers.get("Content-Type"),
                           "redirect_location": response.headers.get("Location"),
                           "authorization_or_user_cookies": False})
    save()
    return response


async def browsers():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        page = await context.new_page()
        async def capture(label, target):
            response = await target.goto(label, wait_until="domcontentloaded", timeout=45000)
            await target.wait_for_timeout(8000)
            record = {"url_requested": label, "url_final": target.url,
                      "http_status": response.status if response else None,
                      "text": (await target.locator("body").inner_text())[:2200]}
            report["browser"].append(record)
            save()
            return record
        await capture(ORIGIN + "/index", page)
        try:
            await page.get_by_text("深圳市一博科技股份有限公司", exact=True).first.click(timeout=10000)
            await page.wait_for_timeout(5000)
            route = page.url
            report["browser"].append({"navigation": "click_public_fair_from_home", "url_final": route,
                                      "text": (await page.locator("body").inner_text())[:2200]})
            save()
            if urlsplit(route).netloc == urlsplit(ORIGIN).netloc:
                fresh = await context.new_page()
                await capture(route, fresh)
                mobile = await browser.new_context(**p.devices["iPhone 13"], locale="zh-CN", timezone_id="Asia/Shanghai")
                mobile_page = await mobile.new_page()
                await capture(route, mobile_page)
                await mobile.close()
        except Exception as error:
            report["browser"].append({"navigation_error": type(error).__name__})
            save()
        await capture(ORIGIN + "/jobfairSearch", page)
        await browser.close()


def main():
    session = requests.Session()
    try:
        home = read(session, ORIGIN + "/index")
        read(session, ORIGIN + "/jobfairSearch")
        if home.status_code == 200:
            sources = re.findall(r'<script[^>]+src=["\']([^"\']+)', home.text)
            for source in sources:
                url = urljoin(ORIGIN, source)
                if urlsplit(url).netloc != urlsplit(ORIGIN).netloc or "/static/js/app" not in url:
                    continue
                response = read(session, url)
                if response.status_code != 200:
                    continue
                for term in ['jobfairDetail', 'jobfairSearch', 'mode:"history"', 'mode:"hash"']:
                    for occurrence in list(re.finditer(re.escape(term), response.text))[:8]:
                        report["route_snippets"].append({"source": url, "term": term,
                                                         "snippet": response.text[max(0, occurrence.start()-200):occurrence.end()+350]})
                save()
        asyncio.run(browsers())
    finally:
        session.close()
        save()
    print(json.dumps(public_data(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
