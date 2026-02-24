"""
login.py — IREPS login automation with CAPTCHA solving and OTP via webhook.

Uses Playwright's built-in locators (get_by_placeholder, get_by_role, get_by_text)
instead of CSS selectors.

Session strategy:
  1. Check saved session file — if valid (< 20 hrs old), load & verify.
  2. If session is expired/missing/invalid → full login flow.
  3. Save session after successful login.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, date

from playwright.async_api import BrowserContext, Page

import config
import locators as sel
from captcha_solver import CaptchaSolver
from otp_receiver import OTPReceiver

logger = logging.getLogger("ireps.login")


def _load_cached_otp() -> str | None:
    """
    Load cached OTP from disk.
    Returns the OTP string if it was saved less than 24 hours ago, else None.
    """
    try:
        if config.OTP_CACHE_FILE.exists():
            with open(config.OTP_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cached_timestamp = data.get("timestamp", "")
            cached_otp = data.get("otp", "")
            if cached_timestamp and cached_otp:
                cached_time = datetime.fromisoformat(cached_timestamp)
                age = datetime.now() - cached_time
                if age < timedelta(hours=24):
                    hours_ago = age.total_seconds() / 3600
                    logger.info("Found cached OTP (%.1f hours old): %s", hours_ago, cached_otp)
                    return cached_otp
                else:
                    logger.info("Cached OTP is %.1f hours old (>24h) — expired", age.total_seconds() / 3600)
    except Exception as e:
        logger.debug("Could not load cached OTP: %s", e)
    return None


def _save_otp_cache(otp: str):
    """Save OTP to disk with current timestamp for 24-hour reuse."""
    try:
        config.OTP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"otp": otp, "timestamp": datetime.now().isoformat()}
        with open(config.OTP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info("OTP cached at %s (valid for 24 hours)", datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception as e:
        logger.warning("Could not save OTP cache: %s", e)


async def _navigate_to_search_tenders(page: Page):
    """
    Navigate to Search Tenders page which shows the login form if not authenticated.
    Uses multiple strategies in order of reliability.
    """
    logger.info("Navigating to Search Tenders page...")

    # Strategy 1: Click "Search E-Tenders" in the QUICK LINKS sidebar (most reliable)
    try:
        quick_link = page.get_by_text("Search E-Tenders", exact=False)
        if await quick_link.count() > 0:
            await quick_link.first.click()
            await page.wait_for_timeout(3000)
            logger.info("Clicked 'Search E-Tenders' quick link in sidebar")
            return
    except Exception as e:
        logger.debug("Quick link strategy failed: %s", e)

    # Strategy 2: Hover E-Tender → Works → Search Tenders menu
    try:
        logger.info("Trying menu navigation: E-Tender → Works → Search Tenders")
        e_tender_menu = page.get_by_text("E-Tender", exact=False).first
        await e_tender_menu.hover(timeout=5000)
        await page.wait_for_timeout(1000)

        works_link = page.get_by_text("Works", exact=True)
        if await works_link.count() > 0:
            await works_link.first.hover()
            await page.wait_for_timeout(1000)

        search_link = page.get_by_text("Search Tenders", exact=True)
        if await search_link.count() > 0:
            await search_link.first.click()
            await page.wait_for_timeout(3000)
            logger.info("Clicked 'Search Tenders' via menu")
            return
    except Exception as e:
        logger.debug("Menu navigation failed: %s", e)

    # Strategy 3: Direct URL fallback
    logger.info("Using direct URL to navigate to Search Tenders")
    await page.goto(
        "https://www.ireps.gov.in/epsn/searchTender.do",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    await page.wait_for_timeout(3000)


async def _get_locator_root(page: Page):
    """
    Returns the correct locator root — either the page itself or an iframe
    that contains the login form. IREPS embeds the login form inside an
    iframe on some environments.
    """
    # Check if an iframe exists on the page
    frames = page.frames
    if len(frames) > 1:
        # Try each frame (skip the main frame at index 0)
        for frame in frames[1:]:
            try:
                mobile = frame.get_by_placeholder(sel.MOBILE_PLACEHOLDER)
                if await mobile.count() > 0:
                    logger.info("Login form found inside iframe: %s", frame.url)
                    return frame
            except Exception:
                continue

    # Also check via frame_locator in case frames aren't enumerated yet
    try:
        frame_loc = page.frame_locator("iframe").first
        mobile = frame_loc.get_by_placeholder(sel.MOBILE_PLACEHOLDER)
        if await mobile.count() > 0:
            logger.info("Login form found via frame_locator")
            return frame_loc
    except Exception:
        pass

    logger.info("Login form found on main page (no iframe)")
    return page


def _session_is_valid() -> bool:
    """Check if the saved session file exists and is less than SESSION_MAX_AGE_HOURS old."""
    session_path = Path(config.SESSION_FILE)
    if not session_path.exists():
        logger.info("No saved session file found at %s", session_path)
        return False

    age = datetime.now() - datetime.fromtimestamp(session_path.stat().st_mtime)
    if age > timedelta(hours=config.SESSION_MAX_AGE_HOURS):
        logger.info("Session file is %s old (max %dh) — expired", age, config.SESSION_MAX_AGE_HOURS)
        return False

    logger.info("Session file exists and is %s old — potentially valid", age)
    return True


async def _verify_session(page: Page) -> bool:
    """
    Navigate to a protected page and check if we are still logged in.
    Returns True if session is active, False if redirected to login.
    """
    try:
        await page.goto(config.IREPS_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # If the page contains "Authenticate Yourself", session is invalid
        auth_heading = page.get_by_text("Authenticate Yourself")
        if await auth_heading.count() > 0:
            logger.info("Session verification failed — redirected to login page")
            return False

        logger.info("Session verification passed — user is logged in")
        return True
    except Exception as e:
        logger.warning("Session verification error: %s", e)
        return False


async def ensure_session(
    context: BrowserContext,
    page: Page,
    otp_receiver: OTPReceiver,
    captcha_solver: CaptchaSolver,
) -> bool:
    """
    Ensure we have a valid logged-in session.
    Loads saved session if valid, otherwise performs full login.
    Returns True on success.
    """
    if _session_is_valid():
        if await _verify_session(page):
            return True
        logger.info("Saved session is stale — proceeding with fresh login")

    return await _perform_login(context, page, otp_receiver, captcha_solver)


async def _perform_login(
    context: BrowserContext,
    page: Page,
    otp_receiver: OTPReceiver,
    captcha_solver: CaptchaSolver,
    max_login_attempts: int = 2,
) -> bool:
    """
    Full login flow with max 2 login attempts per run.

    Flow per attempt:
      1. Navigate to login page
      2. Fill mobile number
      3. Solve CAPTCHA
      4. Click "Get OTP" (generates OTP on IREPS server)
      5. Get OTP — from cache if available, otherwise from webhook
      6. Fill OTP and click Proceed
      7. Save session + cache OTP

    On cached OTP failure:
      - Clears the cache
      - Retries using the webhook OTP (from the same "Get OTP" click)

    Hard stop after 2 total attempts to avoid burning OTP generations
    (IREPS allows only 2 per hour, same OTP valid for 24 hours).
    """
    logger.info("═══ Starting IREPS login flow (max %d attempts) ═══", max_login_attempts)

    # ── Check for cached OTP from today ──────────────────────
    cached_otp = _load_cached_otp()
    otp_generations = 0  # track how many times we clicked "Get OTP"

    for attempt in range(1, max_login_attempts + 1):
        try:
            logger.info("── Login attempt %d/%d ──", attempt, max_login_attempts)

            # Step 1: Navigate directly to the login page
            logger.info("Step 1: Navigating to IREPS login page...")
            await page.goto(config.IREPS_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Detect whether form lives on the main page or inside an iframe
            root = await _get_locator_root(page)

            # Step 2: Fill mobile number using placeholder text
            logger.info("Step 2: Filling mobile number...")
            mobile_input = root.get_by_placeholder(sel.MOBILE_PLACEHOLDER)
            await mobile_input.wait_for(timeout=30000)
            await mobile_input.fill(config.IREPS_MOBILE)
            logger.info("Mobile number filled: %s****%s", config.IREPS_MOBILE[:3], config.IREPS_MOBILE[-2:])

            # Step 3: Solve CAPTCHA
            logger.info("Step 3: Solving CAPTCHA...")

            # On retry, refresh CAPTCHA by clicking the refresh icon near the CAPTCHA image
            if attempt > 1:
                try:
                    verification_section = root.get_by_text(sel.VERIFICATION_CODE_LABEL).locator("..")
                    refresh_icon = verification_section.locator("img[alt*='refresh'], img[alt*='reload'], a:has(img)").last
                    if await refresh_icon.count() > 0:
                        await refresh_icon.click()
                        await page.wait_for_timeout(2000)
                        logger.info("CAPTCHA image refreshed")
                except Exception as e:
                    logger.debug("Could not find refresh button: %s", e)

            # Find the CAPTCHA image
            captcha_section = root.get_by_text(sel.VERIFICATION_CODE_LABEL).locator("..").locator("img").first

            try:
                await captcha_section.wait_for(timeout=5000)
                captcha_element = captcha_section
            except Exception:
                all_imgs = page.locator("img")
                count = await all_imgs.count()
                captcha_element = None
                for i in range(count):
                    img = all_imgs.nth(i)
                    box = await img.bounding_box()
                    if box and 50 < box["width"] < 300 and 20 < box["height"] < 100:
                        captcha_element = img
                        break
                if not captcha_element:
                    raise RuntimeError("Could not locate CAPTCHA image on page")

            captcha_text = await captcha_solver.solve_from_element(captcha_element)

            captcha_input = root.get_by_placeholder(sel.CAPTCHA_INPUT_PLACEHOLDER)
            await captcha_input.fill(captcha_text)
            logger.info("CAPTCHA text filled: '%s'", captcha_text)

            # Step 4: Prepare webhook THEN click "Get OTP"
            # Mark the request time BEFORE clicking so the webhook can detect
            # OTPs that arrive even during the 3-second page wait.
            otp_receiver.clear_for_new_otp()

            logger.info("Step 4: Clicking 'Get OTP'...")
            get_otp_btn = root.get_by_role("button", name=sel.GET_OTP_BUTTON_TEXT)
            await get_otp_btn.click()
            otp_generations += 1
            logger.info("OTP generation #%d triggered (max 2 per hour on IREPS)", otp_generations)
            await page.wait_for_timeout(3000)

            # Check if CAPTCHA was wrong (page may show an error)
            error_text = root.get_by_text("incorrect").or_(
                root.get_by_text("invalid")
            ).or_(
                root.get_by_text("wrong")
            )
            if await error_text.count() > 0:
                logger.warning("CAPTCHA incorrect on attempt %d/%d", attempt, max_login_attempts)
                if attempt >= max_login_attempts:
                    logger.error("⛔ LOGIN FAILED — CAPTCHA incorrect on all %d attempts", max_login_attempts)
                    raise RuntimeError(f"Login failed — CAPTCHA incorrect on all {max_login_attempts} attempts")
                continue

            # Step 5: Get OTP
            # Strategy: try cached OTP first (fast), fall back to webhook
            otp = None
            used_cache = False

            if cached_otp:
                logger.info("Step 5: Using cached OTP: %s", cached_otp)
                otp = cached_otp
                used_cache = True
            else:
                logger.info("Step 5: Waiting for OTP via SMS Forwarder webhook...")
                otp = otp_receiver.wait_for_otp(timeout=90)
                if not otp:
                    logger.error("⛔ OTP not received within 90s — check that ngrok + SMS Forwarder are running")
                    raise RuntimeError(
                        "OTP not received within 90 seconds. Ensure:\n"
                        "  1. ngrok is running: ngrok http 5050\n"
                        "  2. SMS Forwarder URL is set to: <ngrok-url>/sms-webhook\n"
                        "  3. Phone has network connectivity"
                    )
                # Save OTP to cache IMMEDIATELY after receiving from webhook
                # (before even trying to login, so it's available for next run)
                _save_otp_cache(otp)
                logger.info("OTP received from webhook and cached: %s", otp)

            # Step 6: Fill OTP and click Proceed
            logger.info("Step 6: Filling OTP and clicking Proceed...")
            otp_input = root.get_by_placeholder(sel.OTP_INPUT_PLACEHOLDER)
            await otp_input.fill(otp)

            proceed_btn = root.get_by_role("button", name=sel.PROCEED_BUTTON_TEXT)
            await proceed_btn.click()
            await page.wait_for_timeout(5000)

            # Verify login success
            auth_heading = root.get_by_text("Authenticate Yourself")
            if await auth_heading.count() > 0:
                # Login failed with this OTP
                if used_cache:
                    logger.warning("Cached OTP '%s' failed — clearing cache", cached_otp)
                    cached_otp = None
                    try:
                        config.OTP_CACHE_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass

                    # The "Get OTP" click above already triggered a new OTP to the phone.
                    # Try to get it from the webhook (it may have already arrived).
                    logger.info("Trying to get fresh OTP from webhook (already generated)...")
                    fresh_otp = otp_receiver.wait_for_otp(timeout=60)
                    if fresh_otp and fresh_otp != otp:
                        # Got a different OTP from webhook — try it immediately
                        logger.info("Got fresh OTP from webhook: %s — retrying on same page", fresh_otp)
                        _save_otp_cache(fresh_otp)
                        await otp_input.fill(fresh_otp)
                        await proceed_btn.click()
                        await page.wait_for_timeout(5000)

                        # Re-check login success
                        if await auth_heading.count() == 0:
                            otp = fresh_otp
                            # Fall through to success handling below
                        else:
                            logger.error("⛔ LOGIN FAILED — both cached and fresh OTP rejected")
                            raise RuntimeError(
                                f"Login failed — both cached OTP ({otp}) and fresh OTP ({fresh_otp}) were rejected"
                            )
                    elif fresh_otp:
                        # Same OTP from webhook — it was already tried
                        _save_otp_cache(fresh_otp)
                        logger.error(
                            "⛔ LOGIN FAILED — fresh OTP same as cached (%s), already rejected by IREPS",
                            fresh_otp,
                        )
                        raise RuntimeError(
                            f"Login failed — OTP {fresh_otp} rejected by IREPS. "
                            "The OTP may have expired (>24h). Wait for the next OTP cycle."
                        )
                    else:
                        logger.error("⛔ LOGIN FAILED — cached OTP failed and no fresh OTP received")
                        raise RuntimeError(
                            "Login failed — cached OTP expired and could not receive "
                            "fresh OTP via webhook. Check ngrok + SMS Forwarder setup."
                        )
                else:
                    # Webhook OTP also failed
                    logger.error("⛔ LOGIN FAILED — OTP from webhook rejected by IREPS")
                    raise RuntimeError(f"Login failed — OTP {otp} (from webhook) rejected by IREPS")

            # ── Login successful! ────────────────────────────────
            logger.info("✓ Login successful — current URL: %s", page.url)

            # Save session + ensure OTP is cached
            logger.info("Step 7: Saving session state...")
            await context.storage_state(path=str(config.SESSION_FILE))
            logger.info("Session saved to %s", config.SESSION_FILE)

            if not used_cache:
                # Already cached above when received from webhook
                pass
            else:
                # Cached OTP worked — refresh the cache timestamp
                _save_otp_cache(otp)

            logger.info("═══ IREPS login completed successfully ═══")
            return True

        except RuntimeError as e:
            if attempt >= max_login_attempts:
                logger.error("⛔ LOGIN FAILED after %d attempts: %s", max_login_attempts, e)
                logger.error("⛔ Will NOT retry — stopping to avoid burning OTP generations")
                raise
            logger.warning("Login attempt %d failed: %s — will retry", attempt, e)

    logger.error("⛔ LOGIN FAILED after %d attempts — stopping", max_login_attempts)
    raise RuntimeError(
        f"IREPS login failed after {max_login_attempts} attempts. "
        "Check logs for details. Will not retry to avoid OTP rate limits."
    )
