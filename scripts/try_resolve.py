"""Exercise shared.band_resolver against real SeatGeek + Gemini + Google Search.

    python scripts/try_resolve.py                 # the standard battery below
    python scripts/try_resolve.py "mumford & sons" "tyler the creator"

Hits live APIs and costs Gemini calls, so it's a manual check, not a test.
Firestore is only touched for the alias cache, which fails soft — run it
against the emulator (FIRESTORE_EMULATOR_HOST) to keep runs independent, or
against the real project to see caching kick in on a second run.
"""

import logging
import sys
import time

sys.path.insert(0, ".")

from shared import band_resolver  # noqa: E402

# Each case is what makes it interesting, so a run reads as a report card.
CASES = [
    ("Mumford and Sons", "stylization: 'and' → '&'"),
    ("Earth Wind and Fire", "stylization, buried under tribute acts"),
    ("tila", "typo → Tyla"),
    ("sabrina carpender", "typo → Sabrina Carpenter"),
    ("chase", "genuinely ambiguous"),
    ("wednesday", "real act vs. club nights"),
    ("radiohead", "no SeatGeek listing — tribute acts only"),
    ("bad bunny", "fast path, no LLM"),
    ("kendrick", "nickname → Kendrick Lamar"),
    ("asdfghjkl", "not_found"),
]


def main():
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    cases = [(q, "") for q in sys.argv[1:]] or CASES

    summary = []
    for query, note in cases:
        print(f"\n=== {query!r}  {note}")
        started = time.monotonic()
        result = band_resolver.resolve(query)
        elapsed = time.monotonic() - started

        status = result["status"]
        if status == "confident":
            detail = f'{result["name"]} ({result["slug"]})'
        elif status == "ambiguous":
            detail = result["question"] + " " + ", ".join(
                " — ".join(filter(None, (c["name"], c["description"])))
                for c in result["candidates"]
            )
        else:
            detail = result.get("reason") or "—"
        print(f"  → {status}: {detail}  [{elapsed:.1f}s]")
        summary.append((query, status, detail, elapsed))

    print("\n" + "=" * 100)
    for query, status, detail, elapsed in summary:
        print(f"{query:<22} {status:<10} {elapsed:>5.1f}s  {detail[:60]}")


if __name__ == "__main__":
    main()
