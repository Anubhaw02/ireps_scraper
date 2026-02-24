"""
Inspection script v2: dump detail page structure to a file for analysis.
Focuses on BCT-25-26-306 which should have 17 docs but shows 0.

Output goes to inspect_output.txt
"""
import asyncio
import config
from playwright.async_api import async_playwright


DETAIL_URL = (
    "https://www.ireps.gov.in/epsn/nitViewAnonyms/rfq/nitPublish.do"
    "?tenderAnonymsOid=0JNk+QWGvAtZ0BnX/lHqBg==&activity=viewNIT"
)

OUTPUT_FILE = "inspect_output.txt"


async def main():
    lines = []
    def log(msg=""):
        lines.append(msg)
        print(msg)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        viewport = {"width": 1920, "height": 1080}
        if config.SESSION_FILE.exists():
            context = await browser.new_context(
                storage_state=str(config.SESSION_FILE), viewport=viewport,
            )
            log("Loaded saved session")
        else:
            context = await browser.new_context(viewport=viewport)
            log("No saved session")

        page = await context.new_page()
        log(f"Navigating to: {DETAIL_URL}")
        await page.goto(DETAIL_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            log("networkidle timed out")
        await page.wait_for_timeout(3000)

        log(f"Final URL: {page.url}")
        log(f"Page title: {await page.title()}")

        # Auth check
        auth = page.get_by_text("Authenticate Yourself")
        if await auth.count() > 0:
            log("AUTH REDIRECT — session expired")
            await browser.close()
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return

        # Check #attach_docs
        log("\n=== #attach_docs TABLE ===")
        attach = page.locator("#attach_docs")
        log(f"Count: {await attach.count()}")

        # Check styled-button-8
        log("\n=== .styled-button-8 BUTTONS ===")
        btns = page.locator(".styled-button-8")
        btn_count = await btns.count()
        log(f"Count: {btn_count}")
        for i in range(btn_count):
            txt = (await btns.nth(i).inner_text()).strip()
            onclick = await btns.nth(i).get_attribute("onclick") or ""
            log(f"  [{i}] text='{txt}' onclick='{onclick}'")

        # Download Tender Doc text
        log("\n=== 'Download Tender Doc' TEXT ===")
        dl = page.get_by_text("Download Tender Doc", exact=False)
        log(f"Count: {await dl.count()}")

        # List of documents attached
        log("\n=== 'List of documents attached' TEXT ===")
        ldoc = page.get_by_text("List of documents attached", exact=False)
        log(f"Count: {await ldoc.count()}")

        # All table IDs
        log("\n=== ALL TABLE IDs ===")
        tables = page.locator("table")
        tc = await tables.count()
        log(f"Total tables: {tc}")
        for i in range(tc):
            tid = await tables.nth(i).get_attribute("id")
            if tid:
                log(f"  table[{i}] id='{tid}'")

        # Key elements
        log("\n=== KEY ELEMENTS ===")
        checks = [
            "#attach_docs", ".styled-button-8",
            "#nitPublishOuter", "#nitPublishInner",
            "text=Tender Type", "text=NIT Details",
            "text=Corrigendum", "text=File Name",
            "text=Document Description",
        ]
        for sel in checks:
            el = page.locator(sel)
            c = await el.count()
            if c > 0:
                log(f"  '{sel}': {c} match(es)")

        # Dump the full page HTML to find the document section
        log("\n=== SEARCHING PAGE HTML FOR DOCUMENT CLUES ===")
        full_html = await page.content()
        
        # Search for key strings
        for search in ["attach_docs", "downloadtenderDoc", "File Name", 
                       "styled-button-8", "List of documents", "window.open"]:
            count = full_html.count(search)
            log(f"  '{search}' appears {count} time(s) in page HTML")

        # If attach_docs not found, dump a section of HTML around "document" mentions
        if "attach_docs" not in full_html:
            log("\n  attach_docs NOT in page HTML at all!")
        else:
            idx = full_html.index("attach_docs")
            snippet = full_html[max(0, idx-200):idx+2000]
            log(f"\n  HTML around 'attach_docs':\n{snippet}")

        if "downloadtenderDoc" not in full_html:
            log("\n  downloadtenderDoc NOT in page HTML at all!")
        else:
            idx = full_html.index("downloadtenderDoc")
            snippet = full_html[max(0, idx-200):idx+500]
            log(f"\n  HTML around 'downloadtenderDoc':\n{snippet}")

        await context.close()
        await browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"\nOutput saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
