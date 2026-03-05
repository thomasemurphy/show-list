import json
import logging
from typing import TypedDict, Optional

import google.generativeai as genai

from shared.config import config

logger = logging.getLogger(__name__)

genai.configure(api_key=config.GEMINI_API_KEY)
_model = genai.GenerativeModel("gemini-2.5-flash-lite")

_SYSTEM_PROMPT = """You are an intent parser for a show/concert alert SMS service.
Parse the user's message and return JSON only — no markdown, no explanation.

Possible intents:
- REGISTER_BAND: user wants to track a band / get alerts for a band
- REMOVE_BAND: user wants to stop tracking a band (e.g. "stop", "unsubscribe", "remove")
- LIST_BANDS: user wants to see which bands they are tracking
- SET_ZIP: user is providing or updating their zip code / location
- HELP: user wants instructions
- UNKNOWN: message doesn't match any of the above

Output schema (JSON):
{
  "intent": "<INTENT>",
  "band": "<band name or null>",
  "zip": "<5-digit zip or null>"
}

Examples:
  "remind me about The Strokes" → {"intent":"REGISTER_BAND","band":"The Strokes","zip":null}
  "add LCD Soundsystem 10001"   → {"intent":"REGISTER_BAND","band":"LCD Soundsystem","zip":"10001"}
  "stop The Strokes"            → {"intent":"REMOVE_BAND","band":"The Strokes","zip":null}
  "STOP The Strokes"            → {"intent":"REMOVE_BAND","band":"The Strokes","zip":null}
  "what bands am I tracking"    → {"intent":"LIST_BANDS","band":null,"zip":null}
  "my zip is 90210"             → {"intent":"SET_ZIP","band":null,"zip":"90210"}
  "help"                        → {"intent":"HELP","band":null,"zip":null}
"""


class ParsedIntent(TypedDict):
    intent: str
    band: Optional[str]
    zip: Optional[str]


_FALLBACK: ParsedIntent = {"intent": "UNKNOWN", "band": None, "zip": None}


def parse(message: str) -> ParsedIntent:
    """Parse an SMS message into a structured intent using Gemini."""
    try:
        response = _model.generate_content(
            f"{_SYSTEM_PROMPT}\n\nUser message: {message}"
        )
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        intent = data.get("intent", "UNKNOWN")
        if intent not in {
            "REGISTER_BAND", "REMOVE_BAND", "LIST_BANDS",
            "SET_ZIP", "HELP", "UNKNOWN",
        }:
            intent = "UNKNOWN"
        return {
            "intent": intent,
            "band": data.get("band") or None,
            "zip": data.get("zip") or None,
        }
    except Exception as exc:
        logger.warning("Gemini parse failed: %s", exc)
        return _FALLBACK
