"""
locators.py — Playwright locator definitions for IREPS pages.

Uses Playwright's built-in locators (get_by_text, get_by_placeholder, get_by_role)
instead of CSS selectors for resilience and readability.

These are derived from the IREPS page screenshots. If the site changes its labels,
update the text strings here — no core logic changes needed.
"""

# ═══════════════════════════════════════════════════════════════
# LOGIN PAGE LOCATORS
# Page: https://www.ireps.gov.in  →  "Authenticate Yourself" form
# ═══════════════════════════════════════════════════════════════

# Text visible on form fields (from screenshot)
MOBILE_PLACEHOLDER = "Enter Mobile No."
CAPTCHA_INPUT_PLACEHOLDER = "Enter Verification Code"
OTP_INPUT_PLACEHOLDER = "Enter OTP"

# Button text
GET_OTP_BUTTON_TEXT = "Get OTP"
PROCEED_BUTTON_TEXT = "Proceed"
RESET_BUTTON_TEXT = "Reset"

# Labels near elements (for locating via proximity)
VERIFICATION_CODE_LABEL = "Verification Code"
MOBILE_NUMBER_LABEL = "Mobile Number"


# ═══════════════════════════════════════════════════════════════
# TENDER LISTING PAGE LOCATORS
# Page: https://www.ireps.gov.in/epsn/anonymSearch.do
# ═══════════════════════════════════════════════════════════════

# Tab button labels
TAB_ACTIVE_CLOSING_TODAY = "Active Tenders Closing Today"
TAB_ALL_ACTIVE = "All Active Tenders"
TAB_RECENTLY_CLOSED = "Recently Closed Tenders"
TAB_CUSTOM_SEARCH = "Custom Search"
TAB_LIVE_ERA = "Live & Upcoming e-RA"
TAB_CLOSED_ERA = "Closed e-RA"

# Results count label pattern
RESULTS_COUNT_TEXT = "Tender search results"

# Column headers in the listing table (for identifying the right table)
LISTING_HEADERS = [
    "Deptt./Rly. Unit",
    "Tender No",
    "Tender Title",
    "Status",
    "Work Area",
    "Due Date/Time",
    "Due Days",
    "Actions",
]

# Column indices (0-based) in the listing table
COL_DEPTT = 0
COL_TENDER_NO = 1
COL_TENDER_TITLE = 2
COL_STATUS = 3
COL_WORK_AREA = 4
COL_DUE_DATE = 5
COL_DUE_DAYS = 6
COL_ACTIONS = 7


# ═══════════════════════════════════════════════════════════════
# TENDER DETAIL PAGE LOCATORS
# These label texts are expected on the detail page (after login).
# Run inspect_locators.py AFTER logging in to verify these labels.
# ═══════════════════════════════════════════════════════════════

# Labels on the detail page — used with page.get_by_text() or
# by finding the label and then grabbing adjacent text.
DETAIL_LABELS = {
    "tender_type": "Tender Type",
    "date_of_issue": "Date of Issue",
    "estimated_value": "Estimated Value",
    "emd_amount": "EMD Amount",
    "document_cost": "Document Cost",
    "contact_officer": "Contact Officer",
    "corrigendum": "Corrigendum",
    "description": "Description",
    "closing_date": "Closing Date",
}
