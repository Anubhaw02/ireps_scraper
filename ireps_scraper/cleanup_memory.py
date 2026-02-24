"""
cleanup_memory.py — One-time script to clean junk URLs and values from tenders_memory.json.
Run: python cleanup_memory.py
"""
import json
from pathlib import Path

MEMORY_FILE = Path("data/tenders_memory.json")

DOC_PATH_PATTERNS = (
    "/ireps/upload/files/",
    "/ireps/upload/WorksCorrigendum/",
    "/ireps/works/pdfdocs/",
)

JUNK_FIELD_SIGNALS = (
    "createOptorDpdw",
    "document.getElementById",
    "Tender Closing Date",
    "Tender Uploading Date",
    "Starting with",
)


def is_junk_value(val: str, tender_no: str = "") -> bool:
    if not val:
        return False
    if val == tender_no:
        return True
    if val.count("\t") > 3:
        return True
    for sig in JUNK_FIELD_SIGNALS:
        if sig in val:
            return True
    return False


with open(MEMORY_FILE, "r", encoding="utf-8") as f:
    memory = json.load(f)

total_link_removed = 0
total_field_cleared = 0

for tender_no, tender in memory.items():
    # Clean doc_links
    old_links = tender.get("doc_links", [])
    new_links = [url for url in old_links if any(p in url for p in DOC_PATH_PATTERNS)]
    removed = len(old_links) - len(new_links)
    if removed > 0:
        total_link_removed += removed
        print(f"{tender_no}: removed {removed} junk doc_links ({len(new_links)} kept)")
    tender["doc_links"] = new_links

    # Clean junk field values
    for field in ["closing_date", "description", "tender_type"]:
        val = tender.get(field, "")
        if val and is_junk_value(val, tender_no):
            print(f"  Clearing junk '{field}' for {tender_no}: {repr(val[:80])}")
            tender[field] = ""
            total_field_cleared += 1

with open(MEMORY_FILE, "w", encoding="utf-8") as f:
    json.dump(memory, f, indent=2, ensure_ascii=False)

print(f"\nDone: removed {total_link_removed} junk links, cleared {total_field_cleared} junk fields.")
