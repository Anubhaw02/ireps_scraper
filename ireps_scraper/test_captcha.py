"""
test_captcha.py — Test the 2captcha API integration with a sample CAPTCHA image.

Usage:
    python test_captcha.py [path_to_captcha_image]

If no image path is provided, a simple test image is generated.
Requires a valid TWOCAPTCHA_API_KEY in .env.
"""

import sys
import base64
import config

from twocaptcha import TwoCaptcha


def test_captcha(image_path: str | None = None):
    print("=" * 50)
    print("2captcha Integration Test")
    print("=" * 50)

    api_key = config.TWOCAPTCHA_API_KEY
    if not api_key or api_key == "your_2captcha_api_key":
        print("\n✗ TWOCAPTCHA_API_KEY not set in .env file!")
        print("  Set your real 2captcha API key and try again.")
        return

    print(f"\n[1] API Key: {api_key[:8]}...{api_key[-4:]}")

    solver = TwoCaptcha(api_key)

    # Check balance
    print("\n[2] Checking account balance...")
    try:
        balance = solver.balance()
        print(f"    Balance: ${balance}")
        if float(balance) < 0.01:
            print("    ⚠ Warning: balance is very low!")
    except Exception as e:
        print(f"    ✗ Balance check failed: {e}")
        return

    if image_path:
        print(f"\n[3] Solving CAPTCHA from file: {image_path}")
        try:
            result = solver.normal(image_path)
            print(f"    ✓ Solved: '{result.get('code', '')}'")
            print(f"    Full result: {result}")
        except Exception as e:
            print(f"    ✗ Solve failed: {e}")
    else:
        print("\n[3] No image file provided.")
        print("    To test with a real CAPTCHA image:")
        print("    python test_captcha.py captcha_screenshot.png")
        print()
        print("    Tip: Use inspect_selectors.py to open the IREPS login page,")
        print("    then manually screenshot the CAPTCHA and save it as a file.")

    print("\n" + "=" * 50)
    print("TEST COMPLETE")
    print("=" * 50)


if __name__ == "__main__":
    image = sys.argv[1] if len(sys.argv) > 1 else None
    test_captcha(image)
