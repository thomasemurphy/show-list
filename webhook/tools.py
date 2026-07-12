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

        On success, also searches the user's zip codes for upcoming shows:
        - upcoming_shows is a (possibly empty) list of {date, venue, city, url}.
        - If upcoming_shows is non-empty, tell the user the band is playing near
          them, naming the date and venue/city of the soonest show(s).
        - If upcoming_shows is empty, tell the user there are no scheduled shows
          near them yet, but you'll alert them when one is announced.
        - If searched_zip is false (user has no zip codes set), don't mention show
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

        zips = user.get("zips") or []
        upcoming_by_url = {}
        for zip_code in zips:
            events = seatgeek.events_for_slug(slug, band, zip_code)
            # Mirrors show-list-web's BandsController#create, which calls
            # ShowChecker for each of the user's zips on add so the dashboard
            # cell is populated immediately rather than waiting for the next
            # daily poller run.
            db.set_show_cache(band, zip_code, events)
            for e in events:
                # Festival times on SeatGeek are placeholders, so send only the
                # date (YYYY-MM-DD) for festivals; full datetime otherwise.
                upcoming_by_url[e["url"]] = {
                    "date": e["datetime_local"][:10] if e["festival"] else e["datetime_local"],
                    "venue": e["venue_name"], "city": e["venue_city"], "url": e["url"],
                    "festival": e["festival"]}
        upcoming = sorted(upcoming_by_url.values(), key=lambda s: s["date"])
        return {"ok": True, "bands": user.get("bands") or [],
                "searched_zip": bool(zips), "upcoming_shows": upcoming}

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

    def add_zip(zip_code: str) -> dict:
        """Add a 5-digit US zip code to this user's list of locations to check.
        Users can track more than one zip (e.g. home and a city they visit often).

        On success, also searches this zip for upcoming shows from every band
        the user already tracks:
        - upcoming_shows is a (possibly empty) list of {band, date, venue, city, url}.
        - If upcoming_shows is non-empty, tell the user which of their bands are
          playing near this new zip, naming the date and venue/city.
        - If upcoming_shows is empty, tell the user nothing's scheduled there yet,
          but they'll be alerted when something is announced.
        """
        logger.info("[tool] add_zip phone=%s zip=%r", phone, zip_code)
        if not zip_code.isdigit() or len(zip_code) != 5:
            return {"ok": False, "reason": "invalid_zip"}
        db.upsert_user(phone)
        db.add_zip(phone, zip_code)
        user = db.get_user(phone) or {}

        bands = user.get("bands") or []
        upcoming_by_key = {}
        # Mirrors show-list-web's ZipsController#create, which checks every
        # tracked band against a newly added zip so the dashboard row is
        # populated immediately rather than waiting for the next daily poller.
        for band in bands:
            slug = seatgeek.resolve_performer(band)
            if not slug:
                continue
            events = seatgeek.events_for_slug(slug, band, zip_code)
            db.set_show_cache(band, zip_code, events)
            for e in events:
                upcoming_by_key[(band, e["url"])] = {
                    "band": band,
                    "date": e["datetime_local"][:10] if e["festival"] else e["datetime_local"],
                    "venue": e["venue_name"], "city": e["venue_city"], "url": e["url"],
                    "festival": e["festival"]}
        upcoming = sorted(upcoming_by_key.values(), key=lambda s: s["date"])
        return {"ok": True, "zips": user.get("zips") or [], "upcoming_shows": upcoming}

    def remove_zip(zip_code: str) -> dict:
        """Remove a zip code from this user's list of locations. Returns the updated list."""
        logger.info("[tool] remove_zip phone=%s zip=%r", phone, zip_code)
        user = db.get_user(phone) or {}
        current = user.get("zips") or []
        if zip_code not in current:
            return {"ok": False, "reason": "not_tracking", "zips": current}
        db.remove_zip(phone, zip_code)
        user = db.get_user(phone) or {}
        return {"ok": True, "zips": user.get("zips") or []}

    def list_bands() -> dict:
        """Return the user's current tracking list and zip codes."""
        logger.info("[tool] list_bands phone=%s", phone)
        user = db.get_user(phone) or {}
        return {"bands": user.get("bands") or [], "zips": user.get("zips") or []}

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
        zips = user.get("zips") or []
        bands = user.get("bands") or []
        if not zips:
            return {"ok": False, "reason": "no_zip"}
        if not bands:
            return {"ok": False, "reason": "no_bands"}

        # A band can turn up under more than one zip (overlapping search
        # radii); dedup by (band, url) before returning.
        shows_by_key = {}
        for band in bands:
            slug = seatgeek.resolve_performer(band)
            if not slug:
                continue
            for zip_code in zips:
                for e in seatgeek.events_for_slug(slug, band, zip_code):
                    shows_by_key[(band, e["url"])] = {
                        "band": band,
                        # Festival times are placeholders, so send date-only for them.
                        "date": e["datetime_local"][:10] if e["festival"] else e["datetime_local"],
                        "venue": e["venue_name"],
                        "city": e["venue_city"],
                        "url": e["url"],
                        "festival": e["festival"],
                    }
        shows = sorted(shows_by_key.values(), key=lambda s: s["date"])
        return {"ok": True, "shows": shows}

    return [add_band, remove_band, add_zip, remove_zip, list_bands, list_upcoming_shows]
