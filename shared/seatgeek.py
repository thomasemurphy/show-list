import logging
import re
from functools import lru_cache
from typing import Optional

import requests

from shared.config import config

logger = logging.getLogger(__name__)

# For names with no exact match, only accept SeatGeek's top hit if its relevance
# score clears this bar. Real touring acts score well above it (Geese 0.64,
# MJ Lenderman 0.73, Phoebe Bridgers 0.79); tribute/cover acts that pollute
# ambiguous queries score below it (e.g. "The Radiohead Trip" 0.39), so we'd
# rather match nothing than silently track a tribute.
_MIN_FUZZY_SCORE = 0.5

# find_performer / find_events are memoized so the same work isn't repeated when
# multiple users track the same band. The poller is a one-shot Cloud Run Job
# (fresh process per scheduled run), so those caches live for exactly one poll
# cycle and never go stale across days. The webhook is long-running, so it calls
# the *uncached* resolve_performer directly (add-time validation) rather than the
# memoized wrappers, to avoid caching stale or transient-failure results across
# requests.

_BASE = "https://api.seatgeek.com/2"
_AUTH = {
    "client_id": config.SEATGEEK_CLIENT_ID,
    "client_secret": config.SEATGEEK_CLIENT_SECRET,
}


def _normalize(name: str) -> str:
    """Lowercase and strip non-alphanumerics, for forgiving name comparison."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def resolve_performer(band_name: str) -> Optional[str]:
    """Return the best SeatGeek performer slug for band_name, or None.

    SeatGeek's default ranking buries real acts under same-named noise (e.g.
    "Wednesday" returns club nights like "Kapture Wednesdays" first), so we sort
    by relevance score, discard junk, and prefer an exact name match — falling
    back to the top hit only when we're confident. This avoids silently tracking
    the wrong act (a tribute band, a recurring club night, a theater company).

    Uncached. The webhook calls this directly for add-time validation; the poller
    uses the memoized find_performer wrapper below.
    """
    try:
        resp = requests.get(
            f"{_BASE}/performers",
            params={**_AUTH, "q": band_name, "per_page": 10, "sort": "score.desc"},
            timeout=10,
        )
        resp.raise_for_status()
        performers = resp.json().get("performers", [])
    except Exception as exc:
        logger.warning("SeatGeek performer lookup failed for %r: %s", band_name, exc)
        return None

    # Drop pure-junk phantoms: entries with neither relevance nor any events.
    candidates = [
        p for p in performers
        if (p.get("score") or 0) > 0 or ((p.get("stats") or {}).get("event_count") or 0) > 0
    ]
    if not candidates:
        logger.info("No real SeatGeek performer found for %r", band_name)
        return None

    # Prefer an exact (case/punctuation-insensitive) name match. Results are
    # sorted by score desc, so the first exact match is also the most popular.
    target = _normalize(band_name)
    for p in candidates:
        if _normalize(p.get("name", "")) == target:
            logger.info("Performer %r → slug %r (exact match)", band_name, p["slug"])
            return p["slug"]

    # No exact match — only trust the top hit if it's a confident match, else
    # match nothing rather than risk a tribute/cover act.
    best = candidates[0]
    if (best.get("score") or 0) >= _MIN_FUZZY_SCORE:
        logger.info("Performer %r → slug %r (fuzzy, score=%s)",
                    band_name, best["slug"], best.get("score"))
        return best["slug"]

    logger.info("No confident SeatGeek match for %r (best=%r, score=%s) — skipping",
                band_name, best.get("name"), best.get("score"))
    return None


@lru_cache(maxsize=None)
def find_performer(band_name: str) -> Optional[str]:
    """Per-run memoized wrapper around resolve_performer, used by the poller so a
    band tracked by many users is resolved only once per poll cycle."""
    return resolve_performer(band_name)


@lru_cache(maxsize=None)
def find_events(band_name: str, zip_code: str, range_mi: int = 50) -> list[dict]:
    """
    Return upcoming events for band_name near zip_code within range_mi miles.

    Each event dict has:
        id, title, datetime_local, venue_name, venue_city, url, band

    Memoized per run on (band_name, zip_code, range_mi): the event search is
    location-dependent, so it's shared only among users in the same area
    tracking the same band. The returned list is treated as read-only by
    callers (poller.main only iterates it).
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
