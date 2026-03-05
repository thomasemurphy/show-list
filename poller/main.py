"""
Cloud Run Job entrypoint — polls SeatGeek for each user's bands and sends alerts.

Run locally:
    python -m poller.main

Set DRY_RUN=true to log without sending messages.
"""
import logging
import os
import sys

from shared import db
from poller import seatgeek, notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def run() -> None:
    if DRY_RUN:
        logger.info("DRY RUN mode — no messages will be sent")

    users = db.get_all_users()
    logger.info("Loaded %d users", len(users))

    alerts_sent = 0
    errors = 0

    for user in users:
        phone = user["_phone"]
        zip_code = user.get("zip")
        bands = user.get("bands") or []

        if not zip_code:
            logger.info("Skipping %s — no zip code set", phone)
            continue

        if not bands:
            logger.info("Skipping %s — no bands tracked", phone)
            continue

        for band in bands:
            events = seatgeek.find_events(band, zip_code)
            for event in events:
                event_id = event["id"]
                if db.alert_sent(phone, event_id):
                    logger.debug("Already sent alert for %s / event %s", phone, event_id)
                    continue

                success = notifier.send_alert(user, event, dry_run=DRY_RUN)
                if success:
                    if not DRY_RUN:
                        db.record_alert(phone, event_id, event["title"])
                    alerts_sent += 1
                else:
                    errors += 1

    logger.info("Done. Alerts sent: %d, errors: %d", alerts_sent, errors)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    run()
