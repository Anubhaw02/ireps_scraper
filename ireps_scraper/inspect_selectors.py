"""
inspect_locators.py — Opens IREPS pages in a headed Chromium browser for manual inspection.

Usage:
    python inspect_locators.py

This helps you verify that the text labels in locators.py match the actual page.
Use DevTools (F12) → Elements tab to inspect elements.
"""

import asyncio
from playwright.async_api import async_playwright

IREPS_LOGIN_URL = "https://www.ireps.gov.in"
IREPS_SEARCH_URL = "https://www.ireps.gov.in/epsn/anonymSearch.do"


async def inspect():
    print("=" * 60)
    print("IREPS Page Inspector")
    print("=" * 60)
    print()
    print("This tool opens IREPS pages in a visible browser.")
    print("Use DevTools (F12) to inspect elements and verify that")
    print("locators.py text labels match the actual page.")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # ── Login Page ───────────────────────────────────────
        print("[1/3] Opening LOGIN page...")
        await page.goto(IREPS_LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        print()
        print("   Login page is open. Verify these match locators.py:")
        print('   - Mobile input placeholder: "Enter Mobile No."')
        print('   - CAPTCHA input placeholder: "Enter Verification Code"')
        print('   - OTP input placeholder: "Enter OTP"')
        print('   - Buttons: "Get OTP", "Proceed", "Reset"')
        print()
        print("   Also check how the CAPTCHA image + refresh button are structured.")
        print()
        input("   Press Enter when done inspecting the login page...")

        # ── Tender Listing Page ──────────────────────────────
        print()
        print("[2/3] Opening TENDER LISTING page...")
        await page.goto(IREPS_SEARCH_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        print()
        print("   Tender listing page is open. Verify:")
        print('   - Tab labels: "All Active Tenders", etc.')
        print("   - Table column header names and their order")
        print("   - How pagination works (Next link/button)")
        print("   - Action column links (how detail URLs are structured)")
        print()
        input("   Press Enter when done inspecting the listing page...")

        # ── Detail Page ──────────────────────────────────────
        print()
        print("[3/3] To inspect a DETAIL page:")
        print("   - First, manually log in using the browser above")
        print("   - Then click on any tender's detail/action icon")
        print("   - Check what field labels are used on the detail page:")
        print('     "Tender Type", "Estimated Value", "EMD Amount", etc.')
        print()
        print("   Update DETAIL_LABELS in locators.py with the exact text.")
        print()
        input("   Press Enter to close the browser...")

        await context.close()
        await browser.close()

    print()
    print("Done! Update locators.py if any text labels differ.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(inspect())
