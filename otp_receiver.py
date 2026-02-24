"""
otp_receiver.py — Flask webhook server to receive SMS OTP from Android SMS Forwarder app.

Usage:
    from otp_receiver import OTPReceiver
    receiver = OTPReceiver(port=5050)
    receiver.start()              # starts Flask in a daemon thread
    otp = receiver.wait_for_otp(timeout=90)  # blocks until OTP arrives
"""

import re
import time
import logging
import threading
from pathlib import Path

from flask import Flask, request, jsonify

import config

logger = logging.getLogger("ireps.otp_receiver")

# ── OTP extraction patterns (broadest last) ─────────────────
OTP_PATTERNS = [
    re.compile(r'\b(\d{6})\b'),     # standard 6-digit OTP
    re.compile(r'\b(\d{4,8})\b'),   # fallback: 4-8 digit number
]


class OTPReceiver:
    """Thread-safe Flask webhook that receives SMS via HTTP POST and exposes the latest OTP."""

    def __init__(self, port: int = 5050, secret: str = ""):
        self.port = port
        self.secret = secret
        self._latest_otp: str | None = None
        self._otp_timestamp: float = 0.0
        self._otp_request_time: float = 0.0  # set by clear_for_new_otp()
        self._lock = threading.Lock()
        self._event = threading.Event()   # signals when a new OTP arrives
        self._app = self._create_app()

    # ── Flask app ────────────────────────────────────────────
    def _create_app(self) -> Flask:
        app = Flask(__name__)
        app.logger.setLevel(logging.WARNING)  # silence Flask request logs

        @app.route("/sms-webhook", methods=["GET", "POST"])
        def sms_webhook():
            # ── Collect ALL text from the request, regardless of format ──
            all_text_parts = []

            # Try URL query parameters (e.g. ?msg=..., ?message=...)
            for key in ("msg", "message", "text", "body", "sms"):
                val = request.args.get(key, "")
                if val:
                    all_text_parts.append(val)
            # Also grab ANY query parameter value
            for key, val in request.args.items():
                if val and val not in all_text_parts:
                    all_text_parts.append(val)

            # Try JSON body (POST)
            if request.method == "POST":
                data = request.get_json(force=True, silent=True) or {}
                if isinstance(data, dict):
                    for key, val in data.items():
                        if isinstance(val, str) and val not in all_text_parts:
                            all_text_parts.append(val)
                elif isinstance(data, str):
                    all_text_parts.append(data)

                # Try form data
                for key, val in request.form.items():
                    if val not in all_text_parts:
                        all_text_parts.append(val)

                # Try raw body as fallback
                raw_body = request.get_data(as_text=True)
                if raw_body and raw_body not in all_text_parts:
                    all_text_parts.append(raw_body)

            # Combine all text for logging
            combined_text = " | ".join(all_text_parts)

            logger.info("Webhook received [%s] — raw data: %s", request.method, combined_text[:500])

            # Try to extract OTP from ANY of the text parts
            otp = None
            for part in all_text_parts:
                otp = self._extract_otp(part)
                if otp:
                    break

            if otp:
                with self._lock:
                    self._latest_otp = otp
                    self._otp_timestamp = time.time()
                    self._event.set()
                logger.info("✓ OTP extracted: %s", otp)
                return jsonify({"status": "ok", "otp_received": otp}), 200
            else:
                logger.warning("✗ No OTP found in data: %s", combined_text[:500])
                return jsonify({"status": "error", "detail": "no OTP found in message"}), 200

        @app.route("/get-otp", methods=["GET"])
        def get_otp():
            with self._lock:
                if self._latest_otp and (time.time() - self._otp_timestamp < 300):
                    return jsonify({
                        "otp": self._latest_otp,
                        "age_seconds": int(time.time() - self._otp_timestamp),
                        "timestamp": self._otp_timestamp,
                    }), 200
                return jsonify({"otp": None, "detail": "no recent OTP available", "timestamp": 0}), 200

        @app.route("/health", methods=["GET"])
        def health():
            return jsonify({"status": "running"}), 200

        return app

    # ── OTP extraction ───────────────────────────────────────
    @staticmethod
    def _extract_otp(message: str) -> str | None:
        """Try each pattern in order; return the first match."""
        for pattern in OTP_PATTERNS:
            match = pattern.search(message)
            if match:
                return match.group(1)
        return None

    # ── Public interface ─────────────────────────────────────
    def start(self):
        """Start the Flask server in a daemon background thread."""
        # Check if port is already in use
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("0.0.0.0", self.port))
            sock.close()
        except OSError:
            logger.warning(
                "Port %d is already in use! Attempting to use existing server. "
                "If OTP isn't received, stop any standalone otp_receiver.py first.",
                self.port,
            )
            # Port is taken — try to use the existing server by polling /get-otp
            self._use_existing_server = True
            return

        self._use_existing_server = False
        thread = threading.Thread(
            target=self._app.run,
            kwargs={"host": "0.0.0.0", "port": self.port, "debug": False, "use_reloader": False},
            daemon=True,
            name="otp-webhook",
        )
        thread.start()
        # Give Flask a moment to start
        import time as _time
        _time.sleep(1)
        logger.info("OTP webhook server started on port %d", self.port)

    def clear_for_new_otp(self):
        """
        Call this BEFORE clicking 'Get OTP' on the website.
        Records the request time so we can detect OTPs that arrive
        after this point (even if the OTP value is the same as before).
        """
        with self._lock:
            self._otp_request_time = time.time()
            self._event.clear()
        logger.info("Cleared OTP state — will accept OTPs arriving after %.0f", self._otp_request_time)

    def wait_for_otp(self, timeout: int = 90) -> str | None:
        """
        Block until a new OTP arrives via webhook or timeout expires.
        Falls back to polling existing server, then manual input.
        Returns the OTP string, or None on timeout.

        Uses timestamp-based detection: only accepts OTPs that arrived
        AFTER clear_for_new_otp() was called, so it works even when
        IREPS sends the same OTP value for 24 hours.
        """
        logger.info("Waiting for OTP (timeout=%ds)...", timeout)
        request_time = self._otp_request_time

        # ── Check if OTP already arrived (race condition fix) ────
        # OTP may have arrived between clicking 'Get OTP' and calling
        # this method (the 3-second page wait). Check timestamp.
        with self._lock:
            if self._latest_otp and self._otp_timestamp > request_time:
                logger.info("OTP already arrived before wait started: %s (%.1fs ago)",
                            self._latest_otp, time.time() - self._otp_timestamp)
                return self._latest_otp

        if getattr(self, '_use_existing_server', False):
            # Poll the existing Flask server's /get-otp endpoint
            logger.info("Polling existing server at http://127.0.0.1:%d/get-otp ...", self.port)
            import requests as _requests
            start = time.time()

            while time.time() - start < timeout:
                try:
                    resp = _requests.get(f"http://127.0.0.1:{self.port}/get-otp", timeout=5)
                    data = resp.json()
                    otp = data.get("otp")
                    otp_ts = data.get("timestamp", 0)
                    # Accept OTP only if it arrived AFTER we requested it
                    if otp and otp_ts > request_time:
                        logger.info("OTP received from existing server: %s", otp)
                        with self._lock:
                            self._latest_otp = otp
                            self._otp_timestamp = otp_ts
                        return otp
                except Exception:
                    pass
                time.sleep(3)
        else:
            # Normal mode: wait for event from our own Flask server
            # Event was already cleared in clear_for_new_otp()
            arrived = self._event.wait(timeout=timeout)
            if arrived:
                with self._lock:
                    # Double-check timestamp in case of spurious wake
                    if self._latest_otp and self._otp_timestamp > request_time:
                        logger.info("OTP received via webhook: %s", self._latest_otp)
                        return self._latest_otp

        # ── Fallback: manual input (only in non-headless mode) ─────
        if config.HEADLESS:
            logger.warning(
                "OTP not received via webhook after %ds — skipping manual input (headless mode). "
                "Ensure ngrok + SMS Forwarder are running.",
                timeout,
            )
            return None

        logger.warning("OTP not received via webhook after %ds — falling back to manual input", timeout)
        print("\n" + "=" * 60)
        print("⚠️  OTP not received via SMS Forwarder webhook.")
        print("    Check your phone for the OTP SMS and type it below.")
        print("=" * 60)
        try:
            manual_otp = input("Enter OTP (or press Enter to skip): ").strip()
            if manual_otp and manual_otp.isdigit() and 4 <= len(manual_otp) <= 8:
                with self._lock:
                    self._latest_otp = manual_otp
                    self._otp_timestamp = time.time()
                logger.info("OTP entered manually: %s", manual_otp)
                return manual_otp
            elif manual_otp:
                print(f"Invalid OTP format: '{manual_otp}' — expected 4-8 digits")
                logger.warning("Invalid manual OTP: %s", manual_otp)
            else:
                logger.warning("Manual OTP input skipped")
        except (EOFError, KeyboardInterrupt):
            logger.warning("Manual OTP input cancelled")

        return None

    def get_latest_otp(self) -> str | None:
        """Return the latest OTP if it is less than 5 minutes old."""
        with self._lock:
            if self._latest_otp and (time.time() - self._otp_timestamp < 300):
                return self._latest_otp
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    from config import FLASK_PORT, FLASK_SECRET
    receiver = OTPReceiver(port=FLASK_PORT, secret=FLASK_SECRET)
    print(f"Starting OTP webhook server on http://0.0.0.0:{FLASK_PORT}")
    print("Endpoints:")
    print(f"  POST http://localhost:{FLASK_PORT}/sms-webhook")
    print(f"  GET  http://localhost:{FLASK_PORT}/get-otp")
    print(f"  GET  http://localhost:{FLASK_PORT}/health")
    print("Press Ctrl+C to stop.")
    # Run Flask directly on the main thread (not as daemon) so it stays alive
    receiver._app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)
