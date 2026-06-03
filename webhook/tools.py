"""Tools exposed to the Gemini agent.

Each tool is a per-request closure over `phone`, so the LLM never sees or
supplies the user's phone number — it only sees the arguments relevant to
the action it's taking.

NOTE: do not add `from __future__ import annotations` here. The google-genai
automatic function calling introspects these tool signatures and runs
isinstance() against the parameter annotations; PEP-563 stringized annotations
break that with "isinstance() arg 2 must be a type".
"""

import logging

from shared import db, seatgeek

logger = logging.getLogger(__name__)


def make_tools(phone: str):
    def add_band(band: str) -> dict:
        """Add a band to this user's tracking list. Returns the updated list and
        any upcoming shows near the user right now.

        Before adding, confirms the band exists on our concert data source. If no
        confident match is found, returns ok=false reason=not_found and does NOT
        add it — so tell the user we couldn't find that artist instead of claiming
        they're now tracking it.

        On success, also searches the user's zip for upcoming shows:
        - upcoming_shows is a (possibly empty) list of {date, venue, city, url}.
        - If upcoming_shows is non-empty, tell the user the band is playing near
          them, naming the date and venue/city of the soonest show(s).
        - If upcoming_shows is empty, tell the user there are no scheduled shows
          near them yet, but you'll alert them when one is announced.
        - If searched_zip is false (user has no zip set), don't mention show
          results — ask for their zip instead so we can check.
        """
        logger.info("[tool] add_band phone=%s band=%r", phone, band)
        slug = seatgeek.resolve_performer(band)
        if not slug:
            logger.info("[tool] add_band: no concert-source match for %r", band)
            user = db.get_user(phone) or {}
            return {"ok": False, "reason": "not_found", "band": band,
                    "bands": user.get("bands") or []}
        db.upsert_user(phone)
        db.add_band(phone, band)
        user = db.get_user(phone) or {}

        zip_code = user.get("zip")
        upcoming = []
        if zip_code:
            events = seatgeek.events_for_slug(slug, band, zip_code)
            upcoming = [
                # Festival times on SeatGeek are placeholders, so send only the
                # date (YYYY-MM-DD) for festivals; full datetime otherwise.
                {"date": e["datetime_local"][:10] if e["festival"] else e["datetime_local"],
                 "venue": e["venue_name"], "city": e["venue_city"], "url": e["url"],
                 "festival": e["festival"]}
                for e in events
            ]
        return {"ok": True, "bands": user.get("bands") or [],
                "searched_zip": bool(zip_code), "upcoming_shows": upcoming}

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

    def list_upcoming_shows() -> dict:
        """List upcoming shows near the user across every band they track.

        Use this whenever the user asks what shows are coming up / what's near
        them / when their bands are playing. Searches live (we don't store show
        data between the daily alert run and now). Returns:
        - ok=false reason=no_zip  -> ask the user for their zip so we can check.
        - ok=false reason=no_bands -> they track nothing yet; invite them to add.
        - ok=true with shows: a date-sorted list of {band, date, venue, city,
          url, festival}. If shows is empty, tell them nothing is scheduled near
          them yet but you'll alert them when something is announced.

        Summarize concisely for SMS, one line per show, e.g.
        "Geese - Fri Aug 7 at The Fillmore, San Francisco". If a show has a
        festival value, note it's part of that festival.
        """
        logger.info("[tool] list_upcoming_shows phone=%s", phone)
        user = db.get_user(phone) or {}
        zip_code = user.get("zip")
        bands = user.get("bands") or []
        if not zip_code:
            return {"ok": False, "reason": "no_zip"}
        if not bands:
            return {"ok": False, "reason": "no_bands"}

        shows = []
        for band in bands:
            slug = seatgeek.resolve_performer(band)
            if not slug:
                continue
            for e in seatgeek.events_for_slug(slug, band, zip_code):
                shows.append({
                    "band": band,
                    # Festival times are placeholders, so send date-only for them.
                    "date": e["datetime_local"][:10] if e["festival"] else e["datetime_local"],
                    "venue": e["venue_name"],
                    "city": e["venue_city"],
                    "url": e["url"],
                    "festival": e["festival"],
                })
        shows.sort(key=lambda s: s["date"])
        return {"ok": True, "shows": shows}

    return [add_band, remove_band, set_zip, list_bands, list_upcoming_shows]
