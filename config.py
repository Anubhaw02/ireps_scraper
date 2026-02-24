"""
config.py — Central configuration loaded from .env file.
All credentials and paths are exposed as module-level constants.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ── IREPS Login ──────────────────────────────────────────────
IREPS_MOBILE = os.getenv("IREPS_MOBILE", "")
TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")

# ── Flask OTP Webhook ────────────────────────────────────────
FLASK_PORT = int(os.getenv("FLASK_PORT", "5050"))
FLASK_SECRET = os.getenv("FLASK_SECRET", "change-me")

# ── Paths ────────────────────────────────────────────────────
SESSION_FILE = BASE_DIR / os.getenv("SESSION_FILE", "session/ireps_session.json")
DATA_DIR = BASE_DIR / os.getenv("DATA_DIR", "data/")
LOG_FILE = BASE_DIR / os.getenv("LOG_FILE", "logs/scraper.log")
MEMORY_FILE = DATA_DIR / "tenders_memory.json"
OTP_CACHE_FILE = DATA_DIR / "otp_cache.json"

# ── Browser ──────────────────────────────────────────────────
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# ── URLs ─────────────────────────────────────────────────────
IREPS_LOGIN_URL = "https://www.ireps.gov.in/epsn/guestLogin.do"
IREPS_SEARCH_URL = "https://www.ireps.gov.in/epsn/anonymSearch.do"

# ── Scheduling (IST hours) ───────────────────────────────────
SCHEDULE_HOURS = [6, 13, 19]  # 6 AM, 1 PM, 7 PM IST

# ── Session validity (hours) ─────────────────────────────────
SESSION_MAX_AGE_HOURS = 20

# ── Scraping ─────────────────────────────────────────────────
MIN_DELAY = 2  # seconds between page loads
MAX_DELAY = 4
MAX_RETRIES = 3

# ── Documents ────────────────────────────────────────────────
DOCUMENTS_DIR = DATA_DIR / "documents"

# ── Health Monitoring ────────────────────────────────────────
HEALTH_WEBHOOK_URL = os.getenv("HEALTH_WEBHOOK_URL", "")

# ── Dev limit ────────────────────────────────────────────────
# Set to 0 or None for unlimited (production). Positive int = max tenders to scrape.
MAX_TENDERS_DEV = 0  # 0 = unlimited (production). Set to positive int for dev/testing.

# Ensure directories exist
for d in [SESSION_FILE.parent, DATA_DIR, LOG_FILE.parent, DOCUMENTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
