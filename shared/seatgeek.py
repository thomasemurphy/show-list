import difflib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# resolve_performer_interactive only: how far the top hit must clear the
# runner-up to auto-accept a non-exact match without asking (e.g. "kendrick"
# -> Kendrick Lamar 0.83 vs. next-best 0.0, a clear win). A query like "chase"
# scores several distinct real acts within a few hundredths of each other
# (Chase B 0.71, Chase Matthew 0.59, Chase Atlantic 0.55, ...) — too close to
# guess, so that's routed to the caller as ambiguous instead.
_CONFIDENCE_GAP = 0.15

# resolve_performer_interactive only: candidates scored below this are noise
# not worth surfacing as a suggestion (matches the score>0 junk filter below).
_AMBIGUOUS_FLOOR = 0.15

# _typo_search only: SeatGeek's own search is literal — querying "tila" never
# surfaces "Tyla" (SeatGeek returns zero real candidates; it doesn't do its
# own spelling correction), so a misspelled query that finds nothing falls
# back to retrying a bounded set of likely-typo variants of what was typed.
# This caps how many of those variant queries get sent, so a long band name
# doesn't balloon into hundreds of requests.
_MAX_TYPO_VARIANTS = 20

# _typo_search only: how similar (0-1, difflib ratio) a candidate's name must
# be to what the user actually typed to surface it as a "did you mean" —
# without this, a vowel-swapped variant search could drag in some unrelated
# act that just happens to score well on that one variant.
_MIN_TYPO_SIMILARITY = 0.6

_VOWELS = "aeiouy"

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


def _festival_name(ev: dict) -> Optional[str]:
    """If this event is a festival, return its name, else None.

    SeatGeek marks festival events with type 'music_festival' and lists the
    festival itself as the primary performer (the individual acts are non-primary
    performers on the same event), so a band playing a festival shows up as one
    event whose primary performer is the festival.
    """
    if ev.get("type") != "music_festival":
        return None
    for p in ev.get("performers", []):
        if p.get("type") == "music_festival" and p.get("primary"):
            return p.get("name")
    return ev.get("short_title") or None


def _search_performers(band_name: str) -> list[dict]:
    """Return SeatGeek's performer hits for band_name, sorted by relevance and
    stripped of pure-junk phantoms (entries with neither relevance nor any
    events). Uncached. Shared by _find_best_performer and
    resolve_performer_interactive so both do a single API call.
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
        return []

    return [
        p for p in performers
        if (p.get("score") or 0) > 0 or ((p.get("stats") or {}).get("event_count") or 0) > 0
    ]


def _exact_match(band_name: str, candidates: list[dict]) -> Optional[dict]:
    """Return the first candidate whose name matches band_name exactly
    (case/punctuation-insensitive), or None. Candidates are assumed sorted by
    score desc, so the first exact match is also the most popular.
    """
    target = _normalize(band_name)
    for p in candidates:
        if _normalize(p.get("name", "")) == target:
            return p
    return None


def _find_best_performer(band_name: str) -> Optional[dict]:
    """Return the best-matching SeatGeek performer record for band_name, or None.

    SeatGeek's default ranking buries real acts under same-named noise (e.g.
    "Wednesday" returns club nights like "Kapture Wednesdays" first), so we sort
    by relevance score, discard junk, and prefer an exact name match — falling
    back to the top hit only when we're confident. This avoids silently tracking
    the wrong act (a tribute band, a recurring club night, a theater company).

    Always returns a single best-effort guess (never asks); used for one-shot
    lookups with no user to disambiguate with (the poller, and re-resolving an
    already-tracked band). For add-time flows where a wrong guess would
    silently track the wrong act, use resolve_performer_interactive instead.
    """
    candidates = _search_performers(band_name)
    if not candidates:
        logger.info("No real SeatGeek performer found for %r", band_name)
        return None

    exact = _exact_match(band_name, candidates)
    if exact:
        logger.info("Performer %r → slug %r (exact match)", band_name, exact["slug"])
        return exact

    # No exact match — only trust the top hit if it's a confident match, else
    # match nothing rather than risk a tribute/cover act.
    best = candidates[0]
    if (best.get("score") or 0) >= _MIN_FUZZY_SCORE:
        logger.info("Performer %r → slug %r (fuzzy, score=%s)",
                    band_name, best["slug"], best.get("score"))
        return best

    logger.info("No confident SeatGeek match for %r (best=%r, score=%s) — skipping",
                band_name, best.get("name"), best.get("score"))
    return None


def _typo_variants(band_name: str) -> list[str]:
    """Generate a bounded set of single-edit spelling variants of band_name,
    for when the exact query comes back with no real SeatGeek match. Covers
    the most common typo shapes, in priority order:
      1. an adjacent transposition ("Genesis" -> "Geneiss")
      2. a vowel that sounds right but is spelled wrong ("Tyla" -> "Tila")
      3. a dropped or extra letter ("Radiohead" -> "Radiohed")
    and deliberately skips the much larger space of "insert every letter at
    every position" and "substitute every consonant for every other
    consonant", to keep the request count small. Order matters: if band_name
    is long enough to exceed _MAX_TYPO_VARIANTS, the truncation below keeps
    the higher-value edits.
    """
    seen = {band_name}
    variants = []

    def add(variant):
        if variant and variant not in seen:
            seen.add(variant)
            variants.append(variant)

    for i in range(len(band_name) - 1):
        chars = list(band_name)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        add("".join(chars))

    for i, ch in enumerate(band_name):
        if ch.lower() in _VOWELS:
            for v in _VOWELS:
                if v != ch.lower():
                    add(band_name[:i] + v + band_name[i + 1:])

    for i in range(len(band_name)):
        add(band_name[:i] + band_name[i + 1:])

    return variants[:_MAX_TYPO_VARIANTS]


def _typo_search(band_name: str) -> list[dict]:
    """When the exact query has no real SeatGeek match, retry its likely-typo
    variants (see _typo_variants) in parallel and merge whatever real
    performers they turn up — keeping only ones that are still plausibly
    close to what was actually typed (see _MIN_TYPO_SIMILARITY), since a
    variant search can occasionally surface an unrelated act that just
    happens to score well on that one variant, and this should only offer
    spelling corrections, not new guesses. Returns performer records ranked
    by similarity to band_name combined with SeatGeek relevance — so among a
    few similarly-spelled candidates, the one that's an actual touring act
    outranks an obscure one that merely spells closer (e.g. for "Tila": the
    real, popular "Tyla" outranks "Tilian", which is textually closer but a
    much less relevant match) — or [] if nothing plausible turned up.
    """
    variants = _typo_variants(band_name)
    if not variants:
        return []

    target = _normalize(band_name)
    best_by_slug = {}  # slug -> (performer, similarity)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_search_performers, variant) for variant in variants]
        for future in as_completed(futures):
            for performer in future.result():
                slug = performer.get("slug")
                if not slug:
                    continue
                similarity = difflib.SequenceMatcher(
                    None, target, _normalize(performer.get("name", ""))
                ).ratio()
                if similarity < _MIN_TYPO_SIMILARITY:
                    continue
                if slug not in best_by_slug or similarity > best_by_slug[slug][1]:
                    best_by_slug[slug] = (performer, similarity)

    def rank_key(pair):
        performer, similarity = pair
        # A small floor keeps a legitimate real act (found via events, with
        # no relevance score) from ranking at zero.
        relevance = max(performer.get("score") or 0, 0.05)
        return similarity * relevance

    ranked = sorted(best_by_slug.values(), key=rank_key, reverse=True)
    return [performer for performer, _similarity in ranked]


def resolve_performer_interactive(band_name: str) -> dict:
    """Resolve band_name against SeatGeek with a confidence tier the caller can
    act on, so an ambiguous query becomes a clarifying question instead of a
    silent guess or a flat rejection. Uncached; for add-time flows only (the
    poller and other one-shot lookups should keep using resolve_performer).

    Returns one of:
      {"status": "confident", "slug": ..., "name": ...}
        An exact (case/punctuation-insensitive) name match, or a fuzzy top hit
        that clearly beats the runner-up (e.g. "kendrick" -> Kendrick Lamar,
        0.83 vs. the next candidate's 0.0). Safe to add without asking.
      {"status": "ambiguous", "candidates": [{"slug", "name", "score"}, ...]}
        Several plausible acts with no clear winner (e.g. "chase" -> Chase B,
        Chase Matthew, Chase Atlantic, all within a few hundredths of each
        other). The caller should ask the user which one they mean (or
        whether none match) rather than guess. Up to 4 candidates, score desc.
      {"status": "not_found"}
        Nothing came back with real relevance, including on likely-typo
        variants of what was typed.
    """
    candidates = _search_performers(band_name)

    if candidates:
        exact = _exact_match(band_name, candidates)
        if exact:
            logger.info("Performer %r → slug %r (exact match)", band_name, exact["slug"])
            return {"status": "confident", "slug": exact["slug"], "name": exact["name"]}

        best = candidates[0]
        best_score = best.get("score") or 0
        second_score = (candidates[1].get("score") or 0) if len(candidates) > 1 else 0
        if best_score >= _MIN_FUZZY_SCORE and (best_score - second_score) >= _CONFIDENCE_GAP:
            logger.info("Performer %r → slug %r (fuzzy, score=%s, clear of runner-up %s)",
                        band_name, best["slug"], best_score, second_score)
            return {"status": "confident", "slug": best["slug"], "name": best["name"]}

        suggestions = [p for p in candidates if (p.get("score") or 0) >= _AMBIGUOUS_FLOOR][:4]
        if suggestions:
            logger.info("Ambiguous SeatGeek match for %r — %d candidate(s): %s",
                        band_name, len(suggestions), [p["name"] for p in suggestions])
            return {
                "status": "ambiguous",
                "candidates": [
                    {"slug": p["slug"], "name": p["name"], "score": p.get("score") or 0}
                    for p in suggestions
                ],
            }

    # The exact query came back empty (or nothing on it cleared the ambiguous
    # floor) — SeatGeek's own search is literal, so before giving up, retry
    # likely-typo variants of what was typed (e.g. "Tila" -> "Tyla").
    typo_matches = _typo_search(band_name)
    if typo_matches:
        logger.info("Typo-corrected SeatGeek match for %r — %d candidate(s): %s",
                    band_name, len(typo_matches), [p["name"] for p in typo_matches[:4]])
        return {
            "status": "ambiguous",
            "candidates": [
                {"slug": p["slug"], "name": p["name"], "score": p.get("score") or 0}
                for p in typo_matches[:4]
            ],
        }

    logger.info("No confident, plausible, or typo-corrected SeatGeek match for %r", band_name)
    return {"status": "not_found"}


def resolve_performer(band_name: str) -> Optional[str]:
    """Return the best SeatGeek performer slug for band_name, or None.

    Uncached, always a single best-effort guess (never asks). The webhook
    calls this directly to re-resolve an already-tracked band (add_zip,
    list_upcoming_shows) where there's no ambiguity left to raise — the band
    was already confirmed once via resolve_performer_interactive at add-time.
    The poller uses the memoized find_performer wrapper below.
    """
    record = _find_best_performer(band_name)
    return record["slug"] if record else None


@lru_cache(maxsize=None)
def find_performer(band_name: str) -> Optional[str]:
    """Per-run memoized wrapper around resolve_performer, used by the poller so a
    band tracked by many users is resolved only once per poll cycle."""
    return resolve_performer(band_name)


def events_for_slug(slug: str, band_name: str, zip_code: str, range_mi: int = 50) -> list[dict]:
    """Return upcoming events for an already-resolved performer slug near
    zip_code within range_mi miles. Uncached.

    Each event dict has:
        id, title, datetime_local, venue_name, venue_city, url, band

    band_name is only used to label the returned events. The webhook calls this
    directly at add-time (it already has the slug from resolve_performer); the
    poller goes through the memoized find_events wrapper below.
    """
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
            "festival": _festival_name(ev),
        })

    logger.info("Found %d events for %r near %s", len(events), band_name, zip_code)
    return events


@lru_cache(maxsize=None)
def find_events(band_name: str, zip_code: str, range_mi: int = 50) -> list[dict]:
    """
    Return upcoming events for band_name near zip_code within range_mi miles.

    Memoized per run on (band_name, zip_code, range_mi): the event search is
    location-dependent, so it's shared only among users in the same area
    tracking the same band. The returned list is treated as read-only by
    callers (poller.main only iterates it).
    """
    slug = find_performer(band_name)
    if not slug:
        return []
    return events_for_slug(slug, band_name, zip_code, range_mi)
