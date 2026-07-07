"""Gemini-driven conversation orchestrator.

Each inbound SMS becomes a turn in an ongoing chat. The model sees the
user's current state (bands, zip codes) as part of the system instruction and
the last N turns as chat history, then either replies in plain text or
calls one of the tools in webhook.tools to mutate state.
"""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from shared import db
from shared.config import config
from webhook import tools

logger = logging.getLogger(__name__)
client = genai.Client(api_key=config.GEMINI_API_KEY)

MODEL_NAME = "gemini-2.5-flash"
HISTORY_LIMIT = 10

SYSTEM_INSTRUCTION = """You are the assistant for Show List, a concert-alert SMS service.

What the service does:
- Users tell you bands they want to follow. We poll daily and text them when those bands play near any of their zip codes.

How to behave:
- This is SMS. Replies should be one or two short sentences, ideally under 300 characters. Never use markdown.
- Use the tools to read or change the user's tracked bands and zip codes. Don't claim you did something unless a tool confirmed it.
- Users can track more than one zip code (e.g. home and a city they visit often). Use add_zip/remove_zip for each one individually.
- When the user asks what shows are coming up, what's near them, or when their bands are playing, call list_upcoming_shows and summarize the results concisely (one line per show). Don't guess show dates from memory — always use the tool.
- Don't ask for info you already have. If the user already has zip codes set, don't ask for one again unless they're adding another.
- If a brand-new user just says hi, give a one-sentence pitch and one concrete example ("text a band + zip, like 'Radiohead 90210'").
- If a tool returns ok=false, explain the problem briefly in plain language.
- Whenever add_band succeeds, always tell the user about nearby shows in the same reply: if upcoming_shows is non-empty, name the soonest show's date and venue/city; if it's empty (and searched_zip is true), say there are no shows scheduled near them yet but you'll alert them when one is announced. If searched_zip is false, ask for their zip so you can check. Do this even if they were already tracking the band. If a show has a festival value, mention it's part of that festival (e.g. "as part of Outside Lands").
- Be warm and concise. No emoji unless the user uses them first.

Privacy:
- Every conversation is private to the person texting you right now.
- Never disclose, hint at, or speculate about any information about other users — not their phone numbers, the bands they track, their zip codes, how many users exist, or anything else. Your tools only operate on the current user; you have no visibility into others and must not pretend otherwise.
- If a user asks about other users, politely decline and steer back to what they want for themselves.
"""


def _build_profile(user: dict, channel: str) -> str:
    return (
        "\n\nCurrent user state:\n"
        f"- bands tracked: {user.get('bands') or 'none yet'}\n"
        f"- zip codes: {user.get('zips') or 'none set'}\n"
        f"- channel: {channel}"
    )


def reply(phone: str, channel: str, message: str) -> str:
    user = db.get_user(phone) or {}
    history = user.get("messages") or []

    chat_history = [
        types.Content(role=turn["role"], parts=[types.Part(text=turn["text"])])
        for turn in history
    ]
    chat = client.chats.create(
        model=MODEL_NAME,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION + _build_profile(user, channel),
            tools=tools.make_tools(phone),
        ),
        history=chat_history,
    )

    try:
        response = chat.send_message(message)
        reply_text = (response.text or "").strip()
    except Exception:
        logger.exception("Gemini conversation failed")
        return "Sorry, I hit a snag on my end. Try that again in a moment?"

    if not reply_text:
        reply_text = "Got it."

    new_history = (history + [
        {"role": "user", "text": message},
        {"role": "model", "text": reply_text},
    ])[-HISTORY_LIMIT:]
    db.upsert_user(phone, channel=channel, messages=new_history)

    return reply_text
