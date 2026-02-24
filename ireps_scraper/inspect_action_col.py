"""
Inspector with login: logs into IREPS fresh, navigates to search page,
and prints the action column HTML for Works tenders.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright
import config

async def inspect():
    print("Loading .env config...")
    print(f"  Mobile: {config.IREPS_MOBILE[:3]}****{config.IREPS_MOBILE[-2:]}")
    print(f"  Session file: {config.SESSION_FILE}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Try session first
        ctx_kwargs = {}
        if config.SESSION_FILE.exists():
            with open(config.SESSION_FILE) as f:
                ctx_kwargs["storage_state"] = json.load(f)
            print("Using saved session...")
        else:
            print("No session file found — will scrape as guest")

        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()

        print("Navigating to IREPS Search URL...")
        await page.goto(config.IREPS_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        print(f"Current URL: {page.url}")

        # Check if redirected to login
        if "login" in page.url.lower() or "authenticate" in page.url.lower():
            print("Session expired — got redirected to login.")
            await browser.close()
            return

        # Click All Active Tenders
        tab = page.get_by_text("All Active Tenders", exact=True)
        if await tab.count() > 0:
            await tab.first.click()
            await page.wait_for_timeout(4000)
            print("Clicked 'All Active Tenders'")
        else:
            print("Tab not found, trying partial match...")
            tab = page.get_by_text("All Active")
            if await tab.count() > 0:
                await tab.first.click()
                await page.wait_for_timeout(4000)

        # Print page title / verify we're on right page
        title = await page.title()
        print(f"Page title: {title}")

        # Get ALL tables and look for the one with tender data
        tables = page.locator("table")
        count = await tables.count()
        print(f"Found {count} tables on page")

        target_table = None
        for i in range(count):
            tbl = tables.nth(i)
            txt = await tbl.inner_text()
            if "Tender No" in txt and "Work Area" in txt:
                target_table = tbl
                print(f"Found tender table at index {i}")
                break

        if not target_table:
            print("No tender table found. Printing page outer HTML snippet:")
            html = await page.content()
            print(html[:3000])
            await browser.close()
            return

        rows = target_table.locator("tr")
        row_count = await rows.count()
        print(f"Rows in table: {row_count}")

        printed = 0
        for i in range(row_count):
            row = rows.nth(i)
            cells = row.locator("td")
            cell_count = await cells.count()
            if cell_count < 7:
                continue

            work_area = (await cells.nth(4).inner_text()).strip()
            tender_no = (await cells.nth(1).inner_text()).strip()
            action_html = await cells.nth(7).inner_html()

            if "works" not in work_area.lower():
                continue

            print(f"\n{'='*60}")
            print(f"Tender: {tender_no}")
            print(f"Work Area: {work_area}")
            print(f"Action HTML:\n{action_html}")

            # Get all anchor attributes
            links = cells.nth(7).locator("a, img, button, input")
            lc = await links.count()
            for j in range(lc):
                el = links.nth(j)
                tag = await el.evaluate("el => el.tagName")
                href = await el.get_attribute("href") or ""
                onclick = await el.get_attribute("onclick") or ""
                src = await el.get_attribute("src") or ""
                print(f"  [{tag}] href={href!r} onclick={onclick!r} src={src!r}")

            printed += 1
            if printed >= 3:
                break

        await browser.close()
        print("\nInspection done.")

asyncio.run(inspect())
