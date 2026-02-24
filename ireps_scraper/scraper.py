"""
scraper.py — Two-phase tender scraping for IREPS using Playwright locators.

Phase 1: Scrape the public tender listing table (all pages).
Phase 2: Visit each tender's detail page (requires session) for additional fields.
         Also downloads attached documents/PDFs for each tender.

Uses Playwright's built-in locators (get_by_text, get_by_role, locator) — no CSS selectors.
"""

import os
import re
import random
import asyncio
import logging
from pathlib import Path
from playwright.async_api import Page, BrowserContext

import config
import locators as sel

logger = logging.getLogger("ireps.scraper")


async def scrape_tenders(page: Page, context: BrowserContext | None = None) -> list[dict]:
    """
    Full two-phase scrape. Returns a list of tender dicts with merged data.
    context is required for Phase 2 document downloading (captures new tabs).
    """
    logger.info("═══ Starting tender scraping ═══")

    # Phase 1: listing table
    listing_tenders = await _scrape_listing(page)
    logger.info("Phase 1 complete: %d Works tenders from listing", len(listing_tenders))

    if not listing_tenders:
        logger.warning("No tenders found in listing — aborting Phase 2")
        return []

    # Phase 2: detail pages + document downloads (requires active session + context)
    # Wrapped in try/except so Phase 1 data is ALWAYS returned even if Phase 2 crashes
    try:
        enriched_tenders = await _scrape_details(page, context, listing_tenders)
        logger.info("Phase 2 complete: %d tenders enriched with detail data", len(enriched_tenders))
    except Exception as e:
        logger.error("Phase 2 failed entirely: %s — returning Phase 1 data only", e)
        enriched_tenders = listing_tenders

    logger.info("═══ Scraping complete: %d total tenders ═══", len(enriched_tenders))
    return enriched_tenders


# ═══════════════════════════════════════════════════════════════
# PHASE 1 — LISTING TABLE
# ═══════════════════════════════════════════════════════════════

async def _scrape_listing(page: Page) -> list[dict]:
    """Navigate to the tender search page and scrape all pages of the listing table."""
    logger.info("Phase 1: Navigating to tender listing page...")

    # Check if we're already on the Search Tender page (post-login redirect)
    current_url = page.url.lower()
    if "searchtender" not in current_url and "search" not in current_url:
        await page.goto(config.IREPS_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
    else:
        logger.info("Already on the Search Tender page — skipping navigation")

    # ── Click "All Active Tenders" tab ──────────────────────────
    await _click_all_active_tenders_tab(page)

    # Log total results
    try:
        results_text = page.get_by_text(sel.RESULTS_COUNT_TEXT)
        if await results_text.count() > 0:
            text = await results_text.first.inner_text()
            logger.info("Results count: %s", text.strip())
    except Exception:
        pass

    all_tenders: list[dict] = []
    page_num = 1
    dev_limit = config.MAX_TENDERS_DEV or 0  # 0 = unlimited

    while True:
        logger.info("Scraping listing page %d...", page_num)

        tenders_on_page = await _extract_table_rows(page)
        logger.info("  Found %d Works tenders on page %d", len(tenders_on_page), page_num)
        all_tenders.extend(tenders_on_page)

        # Dev limit check
        if dev_limit and len(all_tenders) >= dev_limit:
            all_tenders = all_tenders[:dev_limit]
            logger.info("DEV LIMIT reached: %d tenders — stopping listing scrape", dev_limit)
            break

        # Try to go to next page
        has_next = await _click_next_page(page)
        if not has_next:
            logger.info("No more pages — listing scrape complete")
            break

        page_num += 1
        delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
        logger.debug("Waiting %.1fs before next page...", delay)
        await asyncio.sleep(delay)

    return all_tenders


async def _click_all_active_tenders_tab(page: Page):
    """
    Reliably click the 'All Active Tenders' tab.
    Uses multiple strategies and verifies results are loaded.
    """
    logger.info("Selecting 'All Active Tenders' tab...")

    clicked = False

    # Strategy 1: Exact text match
    try:
        tab = page.get_by_text(sel.TAB_ALL_ACTIVE, exact=True)
        if await tab.count() > 0:
            await tab.first.click()
            clicked = True
            logger.info("Clicked '%s' tab (exact text)", sel.TAB_ALL_ACTIVE)
    except Exception as e:
        logger.debug("Strategy 1 failed: %s", e)

    # Strategy 2: Link role
    if not clicked:
        try:
            tab = page.get_by_role("link", name=sel.TAB_ALL_ACTIVE)
            if await tab.count() > 0:
                await tab.first.click()
                clicked = True
                logger.info("Clicked '%s' tab (link role)", sel.TAB_ALL_ACTIVE)
        except Exception as e:
            logger.debug("Strategy 2 failed: %s", e)

    # Strategy 3: Button role
    if not clicked:
        try:
            tab = page.get_by_role("button", name=sel.TAB_ALL_ACTIVE)
            if await tab.count() > 0:
                await tab.first.click()
                clicked = True
                logger.info("Clicked '%s' tab (button role)", sel.TAB_ALL_ACTIVE)
        except Exception as e:
            logger.debug("Strategy 3 failed: %s", e)

    # Strategy 4: Partial text match (in case of slight differences)
    if not clicked:
        try:
            tab = page.get_by_text("All Active", exact=False)
            if await tab.count() > 0:
                await tab.first.click()
                clicked = True
                logger.info("Clicked tab via partial text 'All Active'")
        except Exception as e:
            logger.debug("Strategy 4 failed: %s", e)

    if not clicked:
        logger.warning("Could not find 'All Active Tenders' tab — proceeding with current view")

    # Wait for results to load after clicking the tab
    await page.wait_for_timeout(5000)

    # Verify we have results (not "No Results Found")
    no_results = page.get_by_text("No Results Found", exact=False)
    if await no_results.count() > 0:
        logger.warning("'No Results Found' displayed — tab may not have loaded correctly")
    else:
        logger.info("Results are loading on 'All Active Tenders' tab")


async def _extract_table_rows(page: Page) -> list[dict]:
    """Extract tender data from the listing table on the current page."""
    tenders = []

    try:
        # Find the INNERMOST (smallest) table that contains our expected
        # headers. The IREPS page nests the data table inside an outer wrapper
        # table that also holds the search form, so picking the first match
        # would grab the wrong one.
        tables = page.locator("table")
        table_count = await tables.count()
        target_table = None
        best_len = float("inf")

        for i in range(table_count):
            table = tables.nth(i)
            text = await table.inner_text()
            if "Tender No" in text and "Deptt" in text:
                if len(text) < best_len:
                    best_len = len(text)
                    target_table = table

        if not target_table:
            logger.warning("Could not find the tender listing table")
            return []

        # Get all rows from this table
        rows = target_table.locator("tr")
        row_count = await rows.count()
        logger.debug("Found %d rows in table", row_count)

        for row_idx in range(row_count):
            try:
                row = rows.nth(row_idx)
                cells = row.locator("td")
                cell_count = await cells.count()

                if cell_count < 7:
                    continue  # skip header or incomplete rows

                tender = await _parse_row_cells(cells, cell_count)
                if tender and tender.get("tender_no"):
                    # Filter: only collect 'Works' tenders, skip Goods/Services
                    work_area = tender.get("work_area", "").strip()
                    if work_area.lower() != "works":
                        logger.debug(
                            "Skipping tender %s — Work Area is '%s' (not 'Works')",
                            tender["tender_no"], work_area,
                        )
                        continue
                    tenders.append(tender)

            except Exception as e:
                logger.warning("Failed to parse row %d: %s", row_idx, e)
                continue

    except Exception as e:
        logger.error("Failed to extract table rows: %s", e)

    return tenders


async def _parse_row_cells(cells, cell_count: int) -> dict | None:
    """Parse cells from a single table row into a tender dict."""
    deptt = await _safe_cell_text(cells, sel.COL_DEPTT)
    tender_no = await _safe_cell_text(cells, sel.COL_TENDER_NO)
    tender_title = await _safe_cell_text(cells, sel.COL_TENDER_TITLE)
    status = await _safe_cell_text(cells, sel.COL_STATUS)
    work_area = await _safe_cell_text(cells, sel.COL_WORK_AREA)
    due_date = await _safe_cell_text(cells, sel.COL_DUE_DATE)
    due_days = await _safe_cell_text(cells, sel.COL_DUE_DAYS)

    # ── Early validation: skip junk / non-data rows BEFORE icon lookup ──
    # This prevents noisy CLICK_FAILED warnings on search-form rows,
    # header rows, and other non-tender elements.
    if not tender_no:
        return None

    # Validate tender_no — skip garbage rows (header rows, page text captured as data)
    if len(tender_no) > 50 or "\n" in tender_no:
        return None

    # Skip known non-data text that gets picked up from form/header elements
    _JUNK_TENDER_NOS = {
        "Tender No", "tender no", "Search Tender", "Organization",
        "Select Date", "Tender Closing Date", "Tender Uploading Date",
        "Deptt./Rly. Unit", "Actions",
    }
    if tender_no in _JUNK_TENDER_NOS:
        return None

    # Must have a valid status to be a real tender row
    _VALID_STATUSES = {"published", "active", "closed", "cancelled", "expired"}
    if status and status.lower() not in _VALID_STATUSES:
        return None

    # ── Extract detail URL from Actions column ────────────────────────
    # The Actions column can have 1, 2, or more icons. Icon count varies
    # per tender row, so we CANNOT rely on position (first/last icon).
    #
    # The correct icon to click is always the one containing:
    #   <img title="View Tender Details" src="...View Details.png">
    #
    # Its parent <a> has:
    #   onclick="postRequestNewWindow('/epsn/...', '...')" or similar
    detail_url = ""
    if cell_count > sel.COL_ACTIONS:
        action_cell = cells.nth(sel.COL_ACTIONS)

        # Find the <a> that wraps img[title="View Tender Details"]
        view_detail_img = action_cell.locator('img[title="View Tender Details"]')
        if await view_detail_img.count() > 0:
            # Navigate upward to the parent <a> tag
            parent_link = view_detail_img.locator("xpath=ancestor::a[1]")
            if await parent_link.count() > 0:
                onclick = await parent_link.get_attribute("onclick") or ""
                href = await parent_link.get_attribute("href") or ""

                # IREPS opens detail pages via: postRequestNewWindow('/epsn/...', '...')
                if onclick:
                    m = re.search(r"postRequestNewWindow\(['\"]([^'\"]+)['\"]", onclick)
                    if m:
                        path = m.group(1)
                        detail_url = path if path.startswith("http") else f"https://www.ireps.gov.in{path}"

                # Fallback: real href (not just '#')
                if not detail_url and href and href != "#" and href.strip():
                    detail_url = href if href.startswith("http") else f"https://www.ireps.gov.in{href}"

                logger.debug(
                    "View Details icon onclick=%.80r → detail_url=%r",
                    onclick, detail_url,
                )
            else:
                logger.warning("CLICK_FAILED: img[title='View Tender Details'] found but no parent <a> in row")
        else:
            logger.warning(
                "CLICK_FAILED: img[title='View Tender Details'] NOT FOUND in Actions column for tender: %s",
                tender_no,
            )

    return {
        "deptt_rly_unit": deptt,
        "tender_no": tender_no,
        "tender_title": tender_title,
        "status": status,
        "work_area": work_area,
        "due_date_time": due_date,
        "due_days": due_days,
        "detail_url": detail_url,  # used for navigation only, stripped before save
        # Phase 2 fields (filled later)
        "tender_type": "",
        "date_of_issue": "",
        "estimated_value": "",
        "emd_amount": "",
        "document_cost": "",
        "contact_officer": "",
        "corrigendum": "",
        "description": "",
        "tender_doc_download_url": None,
        "attached_documents": [],
    }


async def _safe_cell_text(cells, index: int) -> str:
    """Safely get inner text from a cell locator by index."""
    try:
        cell = cells.nth(index)
        if await cell.count() > 0:
            text = await cell.inner_text()
            return text.strip()
    except Exception:
        pass
    return ""


async def _click_next_page(page: Page) -> bool:
    """
    Click the Next page button using text-based locators.
    Returns False if no next page exists.
    """
    try:
        # Try various common "Next" patterns
        for text in ["Next", "»", ">"]:
            next_link = page.get_by_role("link", name=text)
            if await next_link.count() > 0:
                # Check if it's disabled/inactive
                el = next_link.first
                classes = await el.get_attribute("class") or ""
                if "disabled" in classes.lower():
                    return False

                await el.click()
                await page.wait_for_timeout(3000)
                return True

        # Also try button role
        next_btn = page.get_by_role("button", name="Next")
        if await next_btn.count() > 0:
            await next_btn.click()
            await page.wait_for_timeout(3000)
            return True

    except Exception as e:
        logger.warning("Pagination click failed: %s", e)

    return False


# ═══════════════════════════════════════════════════════════════
# PHASE 2 — DETAIL PAGES
# ═══════════════════════════════════════════════════════════════

async def _scrape_details(
    page: Page,
    context: BrowserContext | None,
    tenders: list[dict],
) -> list[dict]:
    """
    Phase 2: For each tender, click img[title="View Tender Details"] on the
    listing page to open the authenticated detail page in a new tab, then
    extract detail fields and documents.

    IREPS opens detail pages via JS onclick='postRequestNewWindow(...)' which
    performs a POST request in a new tab. A plain goto() GET does NOT load the
    full page (documents section is missing in the anonymous GET view).

    This function navigates back to the listing, iterates row by row, clicks
    the correct icon, captures the new tab, extracts data, and closes the tab.
    """
    if not context:
        logger.warning("No browser context — cannot open new tabs for Phase 2")
        return tenders

    total = len(tenders)
    enriched = 0
    failed = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3

    # Build lookup: tender_no → tender dict for matching rows on listing
    pending: dict[str, dict] = {}
    for t in tenders:
        tno = t.get("tender_no", "").strip()
        if tno:
            pending[tno] = t

    if not pending:
        return tenders

    # ── Navigate back to listing page for icon clicks ────────────
    logger.info("Phase 2: Navigating back to listing page for icon clicks...")
    await page.goto(config.IREPS_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)
    await _click_all_active_tenders_tab(page)

    page_num = 1

    while pending:
        logger.info(
            "Phase 2: Scanning listing page %d (%d tenders remaining)...",
            page_num, len(pending),
        )

        # ── Find the tender listing table (innermost/smallest match) ──
        tables = page.locator("table")
        table_count = await tables.count()
        target_table = None
        best_len = float("inf")
        for ti in range(table_count):
            table = tables.nth(ti)
            text = await table.inner_text()
            if "Tender No" in text and "Deptt" in text:
                if len(text) < best_len:
                    best_len = len(text)
                    target_table = table

        if not target_table:
            logger.warning("Phase 2: Could not find listing table on page %d", page_num)
            break

        rows = target_table.locator("tr")
        row_count = await rows.count()

        # ── Iterate rows on this listing page ────────────────────
        for row_idx in range(1, row_count):  # skip header row
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "%d consecutive failures — aborting Phase 2",
                    consecutive_failures,
                )
                break

            try:
                row = rows.nth(row_idx)
                cells = row.locator("td")
                cell_count = await cells.count()
                if cell_count < 7:
                    continue

                # Only process Works tenders
                work_area = (await cells.nth(sel.COL_WORK_AREA).inner_text()).strip()
                if work_area.lower() != "works":
                    continue

                tender_no = (await cells.nth(sel.COL_TENDER_NO).inner_text()).strip()
                if tender_no not in pending:
                    continue

                tender = pending[tender_no]
                logger.info(
                    "  Detail %d/%d: %s (row %d, page %d)",
                    total - len(pending) + 1, total, tender_no, row_idx, page_num,
                )

                # ── Find the correct icon: img[title="View Tender Details"] ──
                action_cell = cells.nth(sel.COL_ACTIONS)
                view_icon = action_cell.locator('img[title="View Tender Details"]')

                if await view_icon.count() == 0:
                    logger.warning(
                        "CLICK_FAILED: img[title='View Tender Details'] NOT FOUND "
                        "in Actions column for tender: %s",
                        tender_no,
                    )
                    tender["tender_doc_download_url"] = None
                    tender["attached_documents"] = []
                    del pending[tender_no]
                    failed += 1
                    consecutive_failures += 1
                    continue

                # ── Click icon and process detail page (with retries) ──
                success = False
                detail_page = None

                for retry in range(config.MAX_RETRIES):
                    try:
                        logger.info(
                            "    Clicking View Tender Details icon (attempt %d)...",
                            retry + 1,
                        )

                        # Click the icon and capture the new tab opened by
                        # postRequestNewWindow() JS function
                        async with context.expect_page(timeout=15000) as new_page_info:
                            await view_icon.click()
                        detail_page = await new_page_info.value

                        # ── Wait for nitPublish.do page to fully load ──
                        try:
                            await detail_page.wait_for_load_state(
                                "networkidle", timeout=15000
                            )
                        except Exception:
                            logger.debug(
                                "    networkidle wait timed out for %s — proceeding",
                                tender_no,
                            )
                        await detail_page.wait_for_timeout(2000)

                        page_loaded_ok = (
                            "nitPublish" in detail_page.url
                            or "rfq" in detail_page.url
                        )

                        # Check for auth redirect
                        auth_heading = detail_page.get_by_text("Authenticate Yourself")
                        if await auth_heading.count() > 0:
                            logger.warning(
                                "Session expired during detail scraping "
                                "— returning partial results"
                            )
                            if detail_page != page:
                                await detail_page.close()
                            return tenders

                        # Extract detail fields
                        detail_data = await _extract_detail_fields(detail_page)
                        tender.update({k: v for k, v in detail_data.items() if v})

                        # ── DOCUMENT EXTRACTION ─────────────────────────
                        try:
                            doc_data = await _extract_documents(detail_page, context)
                            tender["tender_doc_download_url"] = doc_data[
                                "tender_doc_download_url"
                            ]
                            tender["attached_documents"] = doc_data[
                                "attached_documents"
                            ]
                            logger.info(
                                "    Collected tender_doc=%s, %d attached doc(s) "
                                "for %s",
                                "YES"
                                if doc_data["tender_doc_download_url"]
                                else "NO",
                                len(doc_data["attached_documents"]),
                                tender_no,
                            )

                            # Warn if page loaded OK but no docs found
                            if (
                                page_loaded_ok
                                and doc_data["tender_doc_download_url"] is None
                                and len(doc_data["attached_documents"]) == 0
                            ):
                                logger.warning(
                                    "    EMPTY_DOCS: %s — page loaded OK but "
                                    "tender_doc=null and attached_documents=[] "
                                    "— needs manual check",
                                    tender_no,
                                )

                        except Exception as doc_err:
                            logger.warning(
                                "    Document extraction failed for %s: %s",
                                tender_no, doc_err,
                            )
                            tender.setdefault("tender_doc_download_url", None)
                            tender.setdefault("attached_documents", [])
                        # ── END DOCUMENT EXTRACTION ─────────────────────

                        success = True
                        break

                    except Exception as e:
                        error_msg = str(e)
                        if "closed" in error_msg.lower() or "target" in error_msg.lower():
                            logger.error(
                                "Browser/page crashed: %s — aborting Phase 2",
                                error_msg[:100],
                            )
                            return tenders

                        logger.warning(
                            "    Detail error for %s (attempt %d): %s",
                            tender_no, retry + 1, e,
                        )
                        if retry < config.MAX_RETRIES - 1:
                            await asyncio.sleep(2 ** (retry + 1))

                    finally:
                        # Always close the detail tab (never close the listing page)
                        if detail_page and detail_page != page:
                            try:
                                await detail_page.close()
                            except Exception:
                                pass
                            detail_page = None

                            # Verify focus has returned to the listing page
                            try:
                                await page.bring_to_front()
                                await page.wait_for_timeout(500)
                            except Exception:
                                pass

                if success:
                    enriched += 1
                    consecutive_failures = 0
                else:
                    failed += 1
                    consecutive_failures += 1
                    tender.setdefault("tender_doc_download_url", None)
                    tender.setdefault("attached_documents", [])

                del pending[tender_no]

                delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
                await asyncio.sleep(delay)

            except Exception as e:
                logger.warning("  Failed to process row %d: %s", row_idx, e)
                continue

        # Stop if too many consecutive failures
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            break

        if not pending:
            break

        # Try next listing page
        has_next = await _click_next_page(page)
        if not has_next:
            logger.info(
                "No more listing pages — %d tenders not found on listing",
                len(pending),
            )
            break
        page_num += 1

    # ── Mark any remaining unfound tenders ────────────────────────
    for tno, tender in pending.items():
        logger.warning(
            "CLICK_FAILED: Tender %s not found on listing pages — skipping", tno
        )
        tender.setdefault("tender_doc_download_url", None)
        tender.setdefault("attached_documents", [])
        failed += 1

    logger.info("Detail scraping: %d enriched, %d failed out of %d", enriched, failed, total)
    return tenders


def _is_junk_value(value: str) -> bool:
    """
    Detect garbage values that accidentally get scraped from form dropdowns or JS code.
    Signs of junk: lots of whitespace/tabs from <option> lists, JS code snippets, etc.
    """
    if not value:
        return False
    # More than 3 tab characters → likely a <select> dropdown dump
    if value.count("\t") > 3:
        return True
    # Contains JS-like code snippets
    if "createOptorDpdw()" in value or "document.getElementById" in value:
        return True
    # Excessively long values are likely scraped from the wrong element
    if len(value) > 500:
        return True
    return False


async def _extract_detail_fields(page: Page) -> dict:
    """
    Extract all available fields from a tender detail page
    using label-based lookups (find label text → get adjacent value).
    Includes field-specific validation to reject mismatched values.
    """
    detail = {}

    for field_name, label_text in sel.DETAIL_LABELS.items():
        try:
            # Find the label on the page
            label = page.get_by_text(label_text, exact=True)
            if await label.count() == 0:
                # Fall back to inexact match if exact fails
                label = page.get_by_text(label_text, exact=False)
                if await label.count() == 0:
                    continue

            # Strategy: the value is typically in the next sibling cell (td)
            # or in a sibling element. Try multiple approaches:
            label_el = label.first

            value_text = ""

            # Approach 1: Parent row, get the next td
            parent_row = label_el.locator("xpath=ancestor::tr[1]")
            if await parent_row.count() > 0:
                tds = parent_row.locator("td")
                td_count = await tds.count()
                if td_count >= 2:
                    # Usually label is in first td, value in second
                    candidate = await tds.nth(1).inner_text()
                    candidate = candidate.strip()
                    if candidate and candidate != label_text and not _is_junk_value(candidate):
                        value_text = candidate

            # Approach 2: Next sibling element (fallback)
            if not value_text:
                next_el = label_el.locator("xpath=following-sibling::*[1]")
                if await next_el.count() > 0:
                    candidate = await next_el.inner_text()
                    candidate = candidate.strip()
                    if candidate and not _is_junk_value(candidate):
                        value_text = candidate

            # ── Field-specific validation ────────────────────
            if not value_text:
                continue

            if field_name == "closing_date":
                # Must look like a date (e.g. "25/02/2026 14:00"), not a tender number
                if "/" not in value_text:
                    logger.debug("Rejected closing_date value (not a date): '%s'", value_text)
                    continue

            if field_name == "description":
                # Reject common header/label text that gets scraped by mistake
                reject_patterns = {"File Name", "file name", "Description", "Sl. No"}
                if value_text in reject_patterns:
                    logger.debug("Rejected description value (header text): '%s'", value_text)
                    continue

            if field_name == "tender_type":
                # Reject if it duplicates the tender_title (wrong cell scraped)
                existing_title = detail.get("tender_title", "")
                if existing_title and value_text == existing_title:
                    logger.debug("Rejected tender_type (same as tender_title): '%s'", value_text)
                    continue

            detail[field_name] = value_text

        except Exception as e:
            logger.debug("Could not extract '%s' (label: '%s'): %s", field_name, label_text, e)

    return detail


# ═══════════════════════════════════════════════════════════════
# DOCUMENT EXTRACTION — modified section start
# ═══════════════════════════════════════════════════════════════

async def _extract_documents(page: Page, context: BrowserContext | None = None) -> dict:
    """
    Extract document data from a tender detail (nitPublish.do) page.

    Returns:
        {
            "tender_doc_download_url": str | None,
            "attached_documents": [
                {"file_name": str, "file_url": str, "description": str},
                ...
            ]
        }
    """
    BASE = "https://www.ireps.gov.in"
    result = {
        "tender_doc_download_url": None,
        "attached_documents": [],
    }

    # ── THING 1: Capture tender doc download URL ─────────────
    try:
        result["tender_doc_download_url"] = await _capture_tender_doc_url(page, context, BASE)
    except Exception as e:
        logger.warning("    Could not capture tender doc download URL: %s", e)
        result["tender_doc_download_url"] = None

    # ── THING 2: Extract attached documents from #attach_docs ─
    try:
        result["attached_documents"] = await _extract_attached_docs(page, BASE)
    except Exception as e:
        logger.warning("    Could not extract attached documents: %s", e)
        result["attached_documents"] = []

    return result


async def _capture_tender_doc_url(
    page: Page, context: BrowserContext | None, base: str
) -> str | None:
    """
    Capture the real PDF URL triggered by the "Download Tender Doc. (Pdf)" button.

    The button's HTML is:
        <div class="styled-button-8" onclick="downloadtenderDoc();">
          <a href="#">Download Tender Doc. (Pdf)</a>
        </div>

    The href="#" is useless — the real URL is generated dynamically by
    downloadtenderDoc() JavaScript, which typically opens a new window/tab
    pointing to the actual PDF.

    Strategy:
      1. Look for the downloadtenderDoc function's source in the page to
         extract the URL directly from the JS code (fastest).
      2. If that fails, click the button and intercept the new tab URL.
    """
    # ── Strategy 1: Extract URL from page JavaScript source ──
    try:
        # Search for downloadtenderDoc function definition in page scripts
        # Common patterns:
        #   window.open('/ireps/works/pdfdocs/...')
        #   document.forms[...].action = '/ireps/works/pdfdocs/...'
        js_url = await page.evaluate("""
            () => {
                // Try to find the URL by looking at all script content
                const scripts = document.querySelectorAll('script');
                for (const script of scripts) {
                    const text = script.textContent || '';
                    if (text.includes('downloadtenderDoc')) {
                        // Pattern 1: window.open('...url...')
                        let m = text.match(/downloadtenderDoc[^}]*window\.open\(['"]([^'"]+)['"]/s);
                        if (m) return m[1];
                        // Pattern 2: .action = '...url...'
                        m = text.match(/downloadtenderDoc[^}]*\.action\s*=\s*['"]([^'"]+)['"]/s);
                        if (m) return m[1];
                        // Pattern 3: href or location = '...url...'
                        m = text.match(/downloadtenderDoc[^}]*(?:href|location)\s*=\s*['"]([^'"]+)['"]/s);
                        if (m) return m[1];
                    }
                }
                return null;
            }
        """)
        if js_url:
            full_url = js_url if js_url.startswith("http") else f"{base}{js_url}"
            logger.info("    Tender doc URL from JS source: %s", full_url)
            return full_url
    except Exception as e:
        logger.debug("    JS source extraction failed: %s", e)

    # ── Strategy 2: Click button and intercept new tab/request ──
    if context:
        try:
            download_btn = page.locator(".styled-button-8").first
            if await download_btn.count() == 0:
                # Fallback: find by text
                download_btn = page.get_by_text("Download Tender Doc", exact=False).first

            if await download_btn.count() > 0:
                logger.debug("    Clicking 'Download Tender Doc' button...")

                # Listen for new page (tab) opening
                try:
                    async with context.expect_page(timeout=10000) as new_page_info:
                        await download_btn.click()
                    new_page = await new_page_info.value
                    await new_page.wait_for_load_state("commit", timeout=10000)
                    tender_doc_url = new_page.url
                    await new_page.close()

                    if tender_doc_url and tender_doc_url not in ("about:blank", "#"):
                        logger.info("    Tender doc URL from new tab: %s", tender_doc_url)
                        return tender_doc_url
                except Exception as tab_err:
                    logger.debug("    New tab capture failed: %s", tab_err)

                # Fallback: check if a download was triggered instead of a new tab
                try:
                    download_url = await page.evaluate("""
                        () => {
                            // Check if downloadtenderDoc set a form action
                            const forms = document.querySelectorAll('form');
                            for (const form of forms) {
                                if (form.action && form.action.includes('pdfdocs')) {
                                    return form.action;
                                }
                            }
                            return null;
                        }
                    """)
                    if download_url:
                        full_url = download_url if download_url.startswith("http") else f"{base}{download_url}"
                        logger.info("    Tender doc URL from form action: %s", full_url)
                        return full_url
                except Exception:
                    pass

        except Exception as e:
            logger.debug("    Download button click strategy failed: %s", e)

    logger.debug("    Could not resolve tender doc download URL")
    return None


async def _extract_attached_docs(page: Page, base: str) -> list[dict]:
    """
    Extract attached document details from the table with id="attach_docs".

    Each data row has:
        <tr>
            <td class="dataText">  ← S.No (ignored)
            <td class="dataText">  ← <a href="#" onclick="window.open('/ireps/...')">filename.pdf</a>
            <td class="dataText">  ← Document Description
        </tr>

    Returns list of:
        {"file_name": str, "file_url": str, "description": str}
    """
    documents = []
    seen_urls: set[str] = set()

    # ── Locate the attach_docs table by id ────────────────────
    attach_table = page.locator("#attach_docs")
    if await attach_table.count() == 0:
        logger.debug("    No #attach_docs table found on page")
        return documents

    rows = attach_table.locator("tr")
    row_count = await rows.count()
    logger.info("    #attach_docs table has %d row(s) (including header)", row_count)

    # ── Process data rows (skip header row at index 0) ────────
    for row_idx in range(1, row_count):
        try:
            row = rows.nth(row_idx)
            cells = row.locator("td")
            cell_count = await cells.count()

            if cell_count < 2:
                continue  # not a valid data row

            # ── File name + URL from the <a> tag ─────────────
            link = cells.nth(1).locator("a").first
            if await link.count() == 0:
                continue  # no link in this row

            file_name = (await link.inner_text()).strip()
            onclick = (await link.get_attribute("onclick") or "").strip()

            # Extract real URL from onclick: window.open('/ireps/upload/...')
            file_url = None
            if onclick:
                m = re.search(r"""window\.open\(['"]([^'"]+)['"]\)""", onclick)

                if m:
                    path = m.group(1)
                    file_url = path if path.startswith("http") else f"{base}{path}"

            # Fallback: try href if it's not "#"
            if not file_url:
                href = (await link.get_attribute("href") or "").strip()
                if href and href != "#" and href != "javascript:void(0)":
                    file_url = href if href.startswith("http") else f"{base}{href}"

            if not file_url:
                logger.debug("    Row %d: could not extract URL for '%s'", row_idx, file_name)
                continue

            # Deduplicate
            if file_url in seen_urls:
                continue
            seen_urls.add(file_url)

            # ── Description from the third td ────────────────
            description = ""
            if cell_count >= 3:
                description = (await cells.nth(2).inner_text()).strip()

            documents.append({
                "file_name": file_name,
                "file_url": file_url,
                "description": description,
            })
            logger.info("    + attached doc: %s → %s", file_name, file_url)

        except Exception as e:
            logger.debug("    Row %d parse error: %s", row_idx, e)
            continue

    logger.info("    Total attached documents: %d", len(documents))
    return documents

# ═══════════════════════════════════════════════════════════════
# DOCUMENT EXTRACTION — modified section end
# ═══════════════════════════════════════════════════════════════

