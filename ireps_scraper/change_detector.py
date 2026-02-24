"""
change_detector.py — Tracks tender changes between scraping runs using a local JSON memory file.

Classifications:
  NEW            — tender_no not seen before
  UPDATED        — any field (except status) changed
  STATUS_CHANGED — status changed (e.g. Active → Closed) — business-critical
  UNCHANGED      — identical to last seen version
"""

import json
import logging
from pathlib import Path
from datetime import datetime

import config

logger = logging.getLogger("ireps.change_detector")


class ChangeDetector:
    """Compare scraped tenders against a persisted JSON memory to detect changes."""

    def __init__(self, memory_path: Path | None = None):
        self.memory_path = memory_path or config.MEMORY_FILE
        self._memory: dict[str, dict] = self._load_memory()

    # ── Load / Save ──────────────────────────────────────────
    def _load_memory(self) -> dict[str, dict]:
        if self.memory_path.exists():
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("Loaded memory with %d tenders from %s", len(data), self.memory_path)
                return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Could not load memory file: %s — starting fresh", e)
        return {}

    def save_memory(self):
        """Persist current memory to disk using atomic write to prevent corruption."""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self.memory_path.with_suffix(".json.tmp")
        bak_path = self.memory_path.with_suffix(".json.bak")

        # Write to temp file first
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._memory, f, indent=2, ensure_ascii=False)

        # Backup existing file before replacing
        if self.memory_path.exists():
            try:
                import shutil
                shutil.copy2(self.memory_path, bak_path)
            except Exception as e:
                logger.warning("Could not create backup: %s", e)

        # Atomic rename (overwrites target on Windows with replace)
        try:
            tmp_path.replace(self.memory_path)
        except OSError:
            # Fallback for edge cases
            import shutil
            shutil.move(str(tmp_path), str(self.memory_path))

        logger.info("Saved memory with %d tenders to %s", len(self._memory), self.memory_path)

    # ── Detect changes ───────────────────────────────────────
    def detect_changes(self, tenders: list[dict]) -> dict:
        """
        Compare scraped tenders against memory.

        Returns:
            {
                "new": [tender, ...],
                "updated": [tender, ...],
                "status_changed": [tender, ...],
                "unchanged": [tender, ...],
                "all": [tender, ...],
                "summary": { ... }
            }

        Each tender in new/updated/status_changed gets an extra '_change_type' key.
        """
        new = []
        updated = []
        status_changed = []
        unchanged = []

        for tender in tenders:
            tender_no = tender.get("tender_no", "").strip()
            if not tender_no:
                continue

            if tender_no not in self._memory:
                # NEW tender
                tender["_change_type"] = "NEW"
                new.append(tender)
                logger.debug("NEW: %s", tender_no)
            else:
                old = self._memory[tender_no]
                changes = self._diff(old, tender)

                if not changes:
                    tender["_change_type"] = "UNCHANGED"
                    unchanged.append(tender)
                elif "status" in changes:
                    # Status change is most critical
                    tender["_change_type"] = "STATUS_CHANGED"
                    tender["_old_status"] = old.get("status", "")
                    tender["_new_status"] = tender.get("status", "")
                    status_changed.append(tender)
                    logger.info(
                        "STATUS_CHANGED: %s — '%s' → '%s'",
                        tender_no, old.get("status"), tender.get("status"),
                    )
                    # Log other changes too
                    for field, (old_val, new_val) in changes.items():
                        if field != "status":
                            logger.info("  Also changed %s: '%s' → '%s'", field, old_val, new_val)
                else:
                    tender["_change_type"] = "UPDATED"
                    tender["_changes"] = {k: {"old": v[0], "new": v[1]} for k, v in changes.items()}
                    updated.append(tender)
                    for field, (old_val, new_val) in changes.items():
                        logger.info("UPDATED %s — %s: '%s' → '%s'", tender_no, field, old_val, new_val)

        summary = {
            "total_scraped": len(tenders),
            "new_count": len(new),
            "updated_count": len(updated),
            "status_changed_count": len(status_changed),
            "unchanged_count": len(unchanged),
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            "Change detection: %d new, %d updated, %d status_changed, %d unchanged (total %d)",
            len(new), len(updated), len(status_changed), len(unchanged), len(tenders),
        )

        return {
            "new": new,
            "updated": updated,
            "status_changed": status_changed,
            "unchanged": unchanged,
            "all": tenders,
            "summary": summary,
        }

    def update_memory(self, tenders: list[dict]):
        """
        Update memory with the latest tender data.
        Call this AFTER a successful export.

        attached_documents are merged with existing memory (union by file_url,
        no duplicates) so that previously captured docs are never lost if
        Phase 2 fails on a re-scrape. tender_doc_download_url is preserved
        if the new scrape returned None but old has a value.

        detail_url is stripped from the saved JSON (used only for navigation).
        """
        for tender in tenders:
            tender_no = tender.get("tender_no", "").strip()
            if not tender_no:
                continue
            # Store a clean copy without internal change tracking keys
            clean = {k: v for k, v in tender.items() if not k.startswith("_")}
            clean["_last_seen"] = datetime.now().isoformat()

            # Strip detail_url from saved JSON (used only for navigation)
            clean.pop("detail_url", None)

            existing = self._memory.get(tender_no, {})

            # Merge tender_doc_download_url: keep old if new is None
            new_doc_url = clean.get("tender_doc_download_url")
            old_doc_url = existing.get("tender_doc_download_url")
            if not new_doc_url and old_doc_url:
                clean["tender_doc_download_url"] = old_doc_url
                logger.debug("Preserving existing tender_doc_download_url for %s", tender_no)

            # Merge attached_documents: keep existing if new scrape returned empty
            old_docs = existing.get("attached_documents", [])
            new_docs = clean.get("attached_documents", [])
            if not new_docs and old_docs:
                # Phase 2 likely failed — keep previous docs
                clean["attached_documents"] = old_docs
                logger.debug("Preserving %d existing attached_documents for %s (new scrape returned none)",
                             len(old_docs), tender_no)
            elif new_docs and old_docs:
                # Merge: union by file_url, preserving order
                merged = list(old_docs)
                seen_urls = {doc.get("file_url") for doc in old_docs if doc.get("file_url")}
                for doc in new_docs:
                    if doc.get("file_url") and doc["file_url"] not in seen_urls:
                        merged.append(doc)
                        seen_urls.add(doc["file_url"])
                clean["attached_documents"] = merged

            self._memory[tender_no] = clean

        self.save_memory()

    # ── Diff ─────────────────────────────────────────────────
    @staticmethod
    def _diff(old: dict, new: dict) -> dict[str, tuple[str, str]]:
        """
        Compare old and new tender dicts.
        Returns { field_name: (old_value, new_value) } for changed fields.
        Ignores internal keys (starting with '_').
        """
        changes = {}
        all_keys = set(old.keys()) | set(new.keys())

        for key in all_keys:
            if key.startswith("_"):
                continue
            # detail_url is navigation-only, stripped before save — skip
            if key == "detail_url":
                continue
            old_val = str(old.get(key, "")).strip()
            new_val = str(new.get(key, "")).strip()
            if old_val != new_val:
                changes[key] = (old_val, new_val)

        return changes
