import logging
from datetime import datetime

from twilio.rest import Client

from shared.config import config

logger = logging.getLogger(__name__)

_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def _format_date(dt_str: str, date_only: bool = False) -> str:
    """Format ISO datetime string to human-readable date.

    date_only drops the time — used for festivals, whose SeatGeek times are
    placeholders (often 3:30 AM), not real set times.
    """
    try:
        dt = datetime.fromisoformat(dt_str)
        fmt = "%a, %b %-d" if date_only else "%a, %b %-d at %-I:%M %p"
        return dt.strftime(fmt)
    except Exception:
        return dt_str


def _build_message(user: dict, event: dict) -> str:
    band = event["band"]
    city = event["venue_city"]
    venue = event["venue_name"]
    url = event["url"]
    festival = event.get("festival")
    date = _format_date(event["datetime_local"], date_only=bool(festival))
    festival_line = f"Part of {festival}\n" if festival else ""
    return (
        f"🎵 {band} is coming to {city}!\n"
        f"{venue} · {date}\n"
        f"{festival_line}"
        f"Tickets: {url}\n\n"
        f"Reply STOP {band} to unsubscribe."
    )


def send_alert(user: dict, event: dict, dry_run: bool = False) -> bool:
    """
    Send an alert message to the user about event.
    Returns True on success. dry_run=True logs but does not send.
    """
    phone: str = user["_phone"]
    channel: str = user.get("channel", "sms")
    body = _build_message(user, event)

    if dry_run:
        logger.info("[DRY RUN] Would send to %s (%s):\n%s", phone, channel, body)
        return True

    try:
        if channel == "whatsapp":
            msg = _client.messages.create(
                to=f"whatsapp:{phone}",
                from_=f"whatsapp:{config.TWILIO_WHATSAPP_NUMBER}",
                body=body,
            )
        else:
            msg = _client.messages.create(
                to=phone,
                from_=config.TWILIO_PHONE_NUMBER,
                body=body,
            )
        logger.info("Sent alert to %s, SID=%s", phone, msg.sid)
        return True
    except Exception as exc:
        logger.error("Failed to send alert to %s: %s", phone, exc)
        return False
