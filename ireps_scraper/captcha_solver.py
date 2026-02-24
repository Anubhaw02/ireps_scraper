"""
captcha_solver.py — Solve CAPTCHA images using the 2captcha API.

Usage:
    from captcha_solver import CaptchaSolver
    solver = CaptchaSolver(api_key="...")
    text = await solver.solve_from_element(captcha_locator)
"""

import base64
import logging
from twocaptcha import TwoCaptcha

logger = logging.getLogger("ireps.captcha_solver")


class CaptchaSolver:
    """Screenshots a CAPTCHA element and solves it via 2captcha."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("2captcha API key is required")
        self._solver = TwoCaptcha(api_key)

    async def solve_from_element(self, element, max_retries: int = 3) -> str:
        """
        Solve a CAPTCHA by screenshotting a Playwright Locator/ElementHandle.

        Args:
            element: Playwright Locator or ElementHandle pointing to the CAPTCHA image.
            max_retries: Number of retries if 2captcha API fails.

        Returns:
            The solved CAPTCHA text string.

        Raises:
            RuntimeError: If all attempts fail.
        """
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info("CAPTCHA solve attempt %d/%d", attempt, max_retries)

                # Screenshot only the CAPTCHA element
                screenshot_bytes = await element.screenshot()
                b64_image = base64.b64encode(screenshot_bytes).decode("utf-8")

                logger.info("Sending CAPTCHA image to 2captcha (%d bytes)...", len(screenshot_bytes))

                # Send to 2captcha API
                result = self._solver.normal(b64_image)
                solved_text = result.get("code", "").strip()

                if not solved_text:
                    raise RuntimeError("2captcha returned empty result")

                logger.info("CAPTCHA solved: '%s'", solved_text)
                return solved_text

            except Exception as e:
                last_error = e
                logger.warning("CAPTCHA attempt %d failed: %s", attempt, e)

        raise RuntimeError(f"CAPTCHA solving failed after {max_retries} attempts: {last_error}")
