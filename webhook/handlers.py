from __future__ import annotations

from shared import db


def handle(phone: str, channel: str, parsed: dict) -> str:
    """Route a parsed intent to the appropriate handler and return a reply string."""
    intent = parsed["intent"]
    band = parsed.get("band")
    zip_code = parsed.get("zip")

    if intent == "REGISTER_BAND":
        return _register_band(phone, channel, band, zip_code)
    elif intent == "REMOVE_BAND":
        return _remove_band(phone, band)
    elif intent == "LIST_BANDS":
        return _list_bands(phone)
    elif intent == "SET_ZIP":
        return _set_zip(phone, zip_code)
    elif intent == "HELP":
        return _help()
    else:
        return "I didn't understand that. Text HELP for instructions."


# ── Intent handlers ───────────────────────────────────────────────────────────

def _register_band(phone: str, channel: str, band: str | None, zip_code: str | None) -> str:
    if not band:
        return "Which band would you like alerts for? Try: 'remind me about The Strokes'"

    user = db.get_user(phone)

    # If a zip was included, update it; otherwise keep existing
    effective_zip = zip_code or (user or {}).get("zip")

    db.upsert_user(phone, channel=channel)
    db.add_band(phone, band)

    if zip_code:
        db.upsert_user(phone, zip=zip_code)

    if effective_zip:
        return f"Got it! I'll alert you when {band} plays near {effective_zip}."
    else:
        return (
            f"Added {band}! Now set your location so I know where to look: "
            f"reply with your zip code (e.g. '10001')."
        )


def _remove_band(phone: str, band: str | None) -> str:
    if not band:
        return "Which band should I remove? Try: 'stop The Strokes'"
    user = db.get_user(phone)
    if not user or band not in (user.get("bands") or []):
        return f"{band} wasn't in your list."
    db.remove_band(phone, band)
    return f"Removed {band} from your alerts."


def _list_bands(phone: str) -> str:
    user = db.get_user(phone)
    if not user:
        return "You're not tracking any bands yet. Text a band name to get started!"
    bands = user.get("bands") or []
    if not bands:
        return "You're not tracking any bands yet. Text a band name to get started!"
    band_list = ", ".join(bands)
    zip_code = user.get("zip", "not set")
    return f"You're tracking: {band_list}\nYour zip: {zip_code}"


def _set_zip(phone: str, zip_code: str | None) -> str:
    if not zip_code or not zip_code.isdigit() or len(zip_code) != 5:
        return "Please send a valid 5-digit US zip code (e.g. '10001')."
    db.upsert_user(phone, zip=zip_code)
    return f"Updated your location to {zip_code}."


def _help() -> str:
    return (
        "Show Alert Service 🎵\n"
        "──────────────────\n"
        "Track bands & get concert alerts near you.\n\n"
        "Commands:\n"
        "  Add a band:    'remind me about The Strokes'\n"
        "  Remove a band: 'stop The Strokes'\n"
        "  List bands:    'what am I tracking'\n"
        "  Set location:  '10001'\n"
        "  Help:          'help'\n\n"
        "Alerts are sent daily when new shows are found."
    )
