"""Tools exposed to the Gemini agent.

Each tool is a per-request closure over `phone`, so the LLM never sees or
supplies the user's phone number — it only sees the arguments relevant to
the action it's taking.
"""

from __future__ import annotations

import logging

from shared import db

logger = logging.getLogger(__name__)


def make_tools(phone: str):
    def add_band(band: str) -> dict:
        """Add a band to this user's tracking list. Returns the updated list."""
        logger.info("[tool] add_band phone=%s band=%r", phone, band)
        db.upsert_user(phone)
        db.add_band(phone, band)
        user = db.get_user(phone) or {}
        return {"ok": True, "bands": user.get("bands") or []}

    def remove_band(band: str) -> dict:
        """Remove a band from this user's tracking list. Returns the updated list."""
        logger.info("[tool] remove_band phone=%s band=%r", phone, band)
        user = db.get_user(phone) or {}
        current = user.get("bands") or []
        if band not in current:
            return {"ok": False, "reason": "not_tracking", "bands": current}
        db.remove_band(phone, band)
        user = db.get_user(phone) or {}
        return {"ok": True, "bands": user.get("bands") or []}

    def set_zip(zip_code: str) -> dict:
        """Set this user's 5-digit US zip code so we know where they live."""
        logger.info("[tool] set_zip phone=%s zip=%r", phone, zip_code)
        if not zip_code.isdigit() or len(zip_code) != 5:
            return {"ok": False, "reason": "invalid_zip"}
        db.upsert_user(phone, zip=zip_code)
        return {"ok": True, "zip": zip_code}

    def list_bands() -> dict:
        """Return the user's current tracking list and zip code."""
        logger.info("[tool] list_bands phone=%s", phone)
        user = db.get_user(phone) or {}
        return {"bands": user.get("bands") or [], "zip": user.get("zip")}

    return [add_band, remove_band, set_zip, list_bands]
