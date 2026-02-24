"""
test_otp_webhook.py — Sends a mock SMS POST to the OTP webhook and verifies round-trip.

Usage:
    1. Start the OTP receiver first (it starts automatically with main.py)
       or run this script which starts its own instance.
    2. python test_otp_webhook.py
"""

import time
import requests
from otp_receiver import OTPReceiver
import config


def test_webhook():
    print("=" * 50)
    print("OTP Webhook Round-Trip Test")
    print("=" * 50)

    # Start a local OTP receiver
    receiver = OTPReceiver(port=config.FLASK_PORT)
    receiver.start()
    time.sleep(1)  # wait for Flask to start

    # Test health endpoint
    print("\n[1] Testing /health endpoint...")
    try:
        r = requests.get(f"http://localhost:{config.FLASK_PORT}/health", timeout=5)
        print(f"    Status: {r.status_code} — {r.json()}")
        assert r.status_code == 200, "Health check failed"
        print("    ✓ Health check passed")
    except Exception as e:
        print(f"    ✗ Health check failed: {e}")
        return

    # Send mock SMS with standard 6-digit OTP
    print("\n[2] Sending mock SMS with 6-digit OTP...")
    mock_sms = {
        "from": "IREPS",
        "message": "Your OTP for IREPS login is 482910. Valid for today only.",
        "timestamp": "2024-01-15T06:00:00",
    }
    try:
        r = requests.post(
            f"http://localhost:{config.FLASK_PORT}/sms-webhook",
            json=mock_sms,
            timeout=5,
        )
        print(f"    Response: {r.status_code} — {r.json()}")
        assert r.json().get("otp_received") == "482910", "OTP extraction failed"
        print("    ✓ OTP correctly extracted: 482910")
    except Exception as e:
        print(f"    ✗ SMS webhook failed: {e}")
        return

    # Retrieve OTP via GET
    print("\n[3] Retrieving OTP via /get-otp...")
    try:
        r = requests.get(f"http://localhost:{config.FLASK_PORT}/get-otp", timeout=5)
        data = r.json()
        print(f"    Response: {data}")
        assert data.get("otp") == "482910", "OTP retrieval failed"
        print("    ✓ OTP retrieved successfully: 482910")
    except Exception as e:
        print(f"    ✗ OTP retrieval failed: {e}")
        return

    # Test with a different OTP format
    print("\n[4] Sending mock SMS with different format...")
    mock_sms_alt = {
        "from": "IREPS-OTP",
        "message": "IREPS: Use code 931547 to verify your login. Do not share.",
        "timestamp": "2024-01-15T06:01:00",
    }
    try:
        r = requests.post(
            f"http://localhost:{config.FLASK_PORT}/sms-webhook",
            json=mock_sms_alt,
            timeout=5,
        )
        assert r.json().get("otp_received") == "931547"
        print(f"    ✓ Alternative format OTP extracted: 931547")
    except Exception as e:
        print(f"    ✗ Alt format test failed: {e}")
        return

    # Test wait_for_otp interface
    print("\n[5] Testing wait_for_otp() interface...")
    otp = receiver.get_latest_otp()
    assert otp == "931547", f"Expected 931547, got {otp}"
    print(f"    ✓ get_latest_otp() returned: {otp}")

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED ✓")
    print("=" * 50)


if __name__ == "__main__":
    test_webhook()
