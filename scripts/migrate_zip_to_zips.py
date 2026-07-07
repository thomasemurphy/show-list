"""One-time backfill: users/{phone}.zip (string) -> users/{phone}.zips (array).

show-list-web moved to supporting multiple zip codes per user, so the poller
and webhook now read/write a `zips` array instead of a single `zip` string.
Existing docs still only have `zip` and need converting once so they aren't
silently skipped by the poller.

Run locally against prod with the right GCP_PROJECT_ID / credentials set:
    python -m scripts.migrate_zip_to_zips          # dry run, prints planned changes
    python -m scripts.migrate_zip_to_zips --apply  # actually writes
"""
import argparse
import logging

from google.cloud import firestore

from shared.config import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run(apply: bool) -> None:
    db = firestore.Client(project=config.GCP_PROJECT_ID)
    users_ref = db.collection("users")

    migrated = 0
    skipped = 0

    for doc in users_ref.stream():
        data = doc.to_dict()
        old_zip = data.get("zip")

        if "zips" in data:
            skipped += 1
            continue
        if not old_zip:
            skipped += 1
            continue

        logger.info("%s: zip=%r -> zips=[%r]", doc.id, old_zip, old_zip)
        migrated += 1
        if apply:
            doc.reference.set(
                {"zips": firestore.ArrayUnion([old_zip]),
                 "zip": firestore.DELETE_FIELD},
                merge=True,
            )

    logger.info("Done. migrated=%d skipped=%d apply=%s", migrated, skipped, apply)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
