from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from shared.config import config

_client: Optional[firestore.Client] = None


def _db() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=config.GCP_PROJECT_ID)
    return _client


# ── Users ────────────────────────────────────────────────────────────────────

def get_user(phone: str) -> Optional[dict]:
    doc = _db().collection("users").document(phone).get()
    return doc.to_dict() if doc.exists else None


def upsert_user(phone: str, **fields) -> None:
    fields.setdefault("created_at", datetime.now(timezone.utc))
    _db().collection("users").document(phone).set(fields, merge=True)


def add_band(phone: str, band: str) -> None:
    _db().collection("users").document(phone).set(
        {"bands": firestore.ArrayUnion([band])},
        merge=True,
    )


def remove_band(phone: str, band: str) -> None:
    _db().collection("users").document(phone).update(
        {"bands": firestore.ArrayRemove([band])}
    )


def add_zip(phone: str, zip_code: str) -> None:
    _db().collection("users").document(phone).set(
        {"zips": firestore.ArrayUnion([zip_code])},
        merge=True,
    )


def remove_zip(phone: str, zip_code: str) -> None:
    _db().collection("users").document(phone).update(
        {"zips": firestore.ArrayRemove([zip_code])}
    )


def get_all_users() -> list[dict]:
    docs = _db().collection("users").stream()
    users = []
    for doc in docs:
        data = doc.to_dict()
        data["_phone"] = doc.id
        users.append(data)
    return users


# ── Deduplication ────────────────────────────────────────────────────────────

def alert_sent(phone: str, event_id: str) -> bool:
    doc_id = f"{phone}_{event_id}"
    doc = _db().collection("alerts_sent").document(doc_id).get()
    return doc.exists


def record_alert(phone: str, event_id: str, event_title: str) -> None:
    doc_id = f"{phone}_{event_id}"
    _db().collection("alerts_sent").document(doc_id).set({
        "sent_at": datetime.now(timezone.utc),
        "event_title": event_title,
    })


# ── Show cache ───────────────────────────────────────────────────────────────
# Shared with show-list-web (Rails), which reads/writes this collection
# directly via its own Firestore credentials — no API endpoint needed.

def _show_cache_key(band_name: str, zip_code: str) -> str:
    from shared.seatgeek import _normalize
    return f"{_normalize(band_name)}_{zip_code}"


def set_show_cache(band_name: str, zip_code: str, events: list[dict]) -> None:
    doc_id = _show_cache_key(band_name, zip_code)
    _db().collection("shows_cache").document(doc_id).set({
        "band_name": band_name,
        "zip": zip_code,
        "events": events,
        "updated_at": datetime.now(timezone.utc),
    })
