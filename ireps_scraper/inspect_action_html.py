"""
inspect_action_html.py — Dumps the full HTML of the Actions column for the
first 3 Works tenders so we can see EXACTLY what attributes each icon has.
Run: python inspect_action_html.py
"""
import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))
import config
import locators as sel

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        if config.SESSION_FILE.exists():
            ctx = await browser.new_context(storage_state=str(config.SESSION_FILE), accept_downloads=True)
            print("Using saved session")
        else:
            ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()

        print("Navigating...")
        await page.goto(config.IREPS_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Click All Active Tenders tab
        tab = page.get_by_text("All Active Tenders", exact=True)
        if await tab.count() > 0:
            await tab.first.click()
            await page.wait_for_timeout(4000)
            print("Clicked All Active Tenders tab")

        # Find the tender table
        tables = page.locator("table")
        count = await tables.count()
        print(f"Found {count} tables")

        rows_inspected = 0
        for ti in range(count):
            tbl = tables.nth(ti)
            hdr = await tbl.inner_text()
            if "Tender No" in hdr and "Work Area" in hdr:
                print(f"\n=== Tender table found (table {ti}) ===")
                rows = tbl.locator("tr")
                row_count = await rows.count()
                for ri in range(1, row_count):  # skip header row
                    row = rows.nth(ri)
                    cells = row.locator("td")
                    cell_count = await cells.count()
                    if cell_count < 6:
                        continue

                    # Check Work Area column
                    work_area_text = (await cells.nth(sel.COL_WORK_AREA).inner_text()).strip()
                    if "Works" not in work_area_text:
                        continue

                    tender_no = (await cells.nth(sel.COL_TENDER_NO).inner_text()).strip()
                    print(f"\n--- Row {ri}: Tender {tender_no} ---")

                    # Dump full HTML of actions cell
                    action_cell = cells.nth(sel.COL_ACTIONS)
                    action_html = await action_cell.inner_html()
                    print(f"Actions cell HTML:\n{action_html}\n")

                    # Check each link
                    links = action_cell.locator("a")
                    link_count = await links.count()
                    print(f"  {link_count} links in action cell:")
                    for li in range(link_count):
                        lnk = links.nth(li)
                        href = await lnk.get_attribute("href") or "(none)"
                        onclick = await lnk.get_attribute("onclick") or "(none)"
                        title = await lnk.get_attribute("title") or "(none)"
                        img_src = ""
                        img = lnk.locator("img")
                        if await img.count() > 0:
                            img_src = await img.first.get_attribute("src") or ""
                        print(f"  Link {li}: href={href!r}  onclick={onclick[:100]!r}  title={title!r}  img={img_src!r}")

                    rows_inspected += 1
                    if rows_inspected >= 3:
                        break
                break

        print("\nDone. Browser staying open — close manually.")
        await page.wait_for_timeout(30000)
        await browser.close()

asyncio.run(main())
