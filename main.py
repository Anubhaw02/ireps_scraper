"""
main.py — Entry point for the IREPS Tender Scraper.

Usage:
    python main.py              # Start scheduler (6 AM, 1 PM, 7 PM IST)
    python main.py --run-now    # Immediate single scrape run
    python main.py --test-login # Test login flow in headed (visible) browser
"""

import sys
import signal
import asyncio
import logging
import argparse
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler


import config
from otp_receiver import OTPReceiver
from captcha_solver import CaptchaSolver
from change_detector import ChangeDetector


logger = logging.getLogger("ireps")


# ── Logging setup ────────────────────────────────────────────
def setup_logging():
    """Configure logging to console + rotating file."""
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Root logger
    root = logging.getLogger("ireps")
    root.setLevel(logging.DEBUG)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root.addHandler(console)

    # File handler (rotate daily, keep 7 days)
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        str(config.LOG_FILE),
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root.addHandler(file_handler)


# ── Core scrape run ──────────────────────────────────────────
async def run_scrape(headless: bool = True):
    """Execute one full scraping cycle: login → scrape → detect changes → export."""
    from playwright.async_api import async_playwright
    from login import ensure_session
    from scraper import scrape_tenders

    start_time = datetime.now()
    logger.info("══════════════════════════════════════════")
    logger.info("SCRAPE RUN STARTED at %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("══════════════════════════════════════════")

    # Initialize components
    otp_receiver = OTPReceiver(port=config.FLASK_PORT, secret=config.FLASK_SECRET)
    otp_receiver.start()
    captcha_solver = CaptchaSolver(api_key=config.TWOCAPTCHA_API_KEY)
    change_detector = ChangeDetector()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        # Load saved session if available — use desktop viewport so nav bar is visible
        viewport = {"width": 1920, "height": 1080}
        if config.SESSION_FILE.exists():
            context = await browser.new_context(
                storage_state=str(config.SESSION_FILE),
                viewport=viewport,
                accept_downloads=True,
            )
            logger.info("Loaded saved session from %s", config.SESSION_FILE)
        else:
            context = await browser.new_context(
                viewport=viewport,
                accept_downloads=True,
            )

        page = await context.new_page()

        try:
            # Step 1: Ensure valid session
            logger.info("Step 1: Ensuring valid session...")
            await ensure_session(context, page, otp_receiver, captcha_solver)

            # Step 2: Scrape tenders (two-phase)
            logger.info("Step 2: Scraping tenders...")
            tenders = await scrape_tenders(page, context)

            if not tenders:
                logger.warning("No tenders scraped — skipping export")
                return

            # Step 3: Detect changes
            logger.info("Step 3: Detecting changes...")
            change_result = change_detector.detect_changes(tenders)

            # Step 4: Update memory (JSON)
            logger.info("Step 4: Updating memory...")
            change_detector.update_memory(tenders)

            # Summary
            elapsed = (datetime.now() - start_time).total_seconds()
            summary = change_result["summary"]
            logger.info("══════════════════════════════════════════")
            logger.info("SCRAPE RUN COMPLETE — %.1f seconds", elapsed)
            logger.info("  Total: %d | New: %d | Updated: %d | Status Changed: %d | Unchanged: %d",
                        summary["total_scraped"], summary["new_count"],
                        summary["updated_count"], summary["status_changed_count"],
                        summary["unchanged_count"])
            logger.info("══════════════════════════════════════════")

            # Health webhook — success notification
            _send_health_webhook(
                status="success",
                message=f"Scrape completed in {elapsed:.0f}s — {summary['total_scraped']} tenders "
                        f"({summary['new_count']} new, {summary['updated_count']} updated, "
                        f"{summary['status_changed_count']} status changed)",
            )

        except Exception as e:
            logger.error("Scrape run failed: %s", e, exc_info=True)
            _send_health_webhook(
                status="failure",
                message=f"Scrape run FAILED: {e}",
            )
            raise
        finally:
            await context.close()
            await browser.close()


def _send_health_webhook(status: str, message: str):
    """Send a health notification via webhook (if configured)."""
    url = config.HEALTH_WEBHOOK_URL
    if not url:
        return
    try:
        import requests
        payload = {
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "source": "ireps_scraper",
        }
        requests.post(url, json=payload, timeout=10)
        logger.info("Health webhook sent (%s)", status)
    except Exception as e:
        logger.warning("Health webhook failed: %s", e)


# ── Test login flow ──────────────────────────────────────────
async def test_login():
    """Run only the login flow in headed (visible) mode for testing."""
    from playwright.async_api import async_playwright
    from login import _perform_login

    logger.info("═══ TEST LOGIN MODE (headed) ═══")

    # Override headless flag so OTP receiver allows manual input fallback
    config.HEADLESS = False

    otp_receiver = OTPReceiver(port=config.FLASK_PORT, secret=config.FLASK_SECRET)
    otp_receiver.start()
    captcha_solver = CaptchaSolver(api_key=config.TWOCAPTCHA_API_KEY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # always headed for test
        context = await browser.new_context()
        page = await context.new_page()

        try:
            success = await _perform_login(context, page, otp_receiver, captcha_solver)
            if success:
                logger.info("✓ Login test PASSED — session saved")
                logger.info("  You can now close the browser or inspect the page.")
                # Keep browser open for inspection
                input("Press Enter to close the browser...")
            else:
                logger.error("✗ Login test FAILED")
        except Exception as e:
            logger.error("✗ Login test error: %s", e, exc_info=True)
        finally:
            await context.close()
            await browser.close()




# ── CLI ──────────────────────────────────────────────────────
def main():
    setup_logging()
    logger.info("IREPS Tender Scraper starting...")

    parser = argparse.ArgumentParser(description="IREPS Tender Scraper")
    parser.add_argument("--run-now", action="store_true", help="Run scraper immediately")
    parser.add_argument("--test-login", action="store_true", help="Test login flow in headed mode")
    args = parser.parse_args()

    # Graceful shutdown
    def shutdown_handler(sig, frame):
        logger.info("Received signal %s — shutting down...", sig)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    if args.test_login:
        asyncio.run(test_login())
    elif args.run_now:
        asyncio.run(run_scrape(headless=config.HEADLESS))
    else:
        parser.print_help()
        print("\nUse --run-now for a single scrape. Schedule externally via cron or cloud scheduler.")


if __name__ == "__main__":
    main()
