import logging
from typing import Optional

import requests

from shared.config import config

logger = logging.getLogger(__name__)

_BASE = "https://api.seatgeek.com/2"
_AUTH = {
    "client_id": config.SEATGEEK_CLIENT_ID,
    "client_secret": config.SEATGEEK_CLIENT_SECRET,
}


def find_performer(band_name: str) -> Optional[str]:
    """Return the best-match SeatGeek performer slug for band_name, or None."""
    try:
        resp = requests.get(
            f"{_BASE}/performers",
            params={**_AUTH, "q": band_name, "per_page": 1},
            timeout=10,
        )
        resp.raise_for_status()
        performers = resp.json().get("performers", [])
        if not performers:
            logger.info("No SeatGeek performer found for %r", band_name)
            return None
        slug = performers[0]["slug"]
        logger.info("Performer %r → slug %r", band_name, slug)
        return slug
    except Exception as exc:
        logger.warning("SeatGeek performer lookup failed for %r: %s", band_name, exc)
        return None


def find_events(band_name: str, zip_code: str, range_mi: int = 50) -> list[dict]:
    """
    Return upcoming events for band_name near zip_code within range_mi miles.

    Each event dict has:
        id, title, datetime_local, venue_name, venue_city, url, band
    """
    slug = find_performer(band_name)
    if not slug:
        return []

    try:
        resp = requests.get(
            f"{_BASE}/events",
            params={
                **_AUTH,
                "performers.slug": slug,
                "postal_code": zip_code,
                "range": f"{range_mi}mi",
                "sort": "datetime_local.asc",
                "per_page": 10,
            },
            timeout=10,
        )
        resp.raise_for_status()
        raw_events = resp.json().get("events", [])
    except Exception as exc:
        logger.warning("SeatGeek event search failed for %r/%s: %s", band_name, zip_code, exc)
        return []

    events = []
    for ev in raw_events:
        venue = ev.get("venue", {})
        events.append({
            "id": str(ev["id"]),
            "title": ev.get("title", band_name),
            "datetime_local": ev.get("datetime_local", ""),
            "venue_name": venue.get("name", ""),
            "venue_city": venue.get("city", ""),
            "url": ev.get("url", ""),
            "band": band_name,
        })

    logger.info("Found %d events for %r near %s", len(events), band_name, zip_code)
    return events
