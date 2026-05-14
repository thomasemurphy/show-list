"""Gemini-driven conversation orchestrator.

Each inbound SMS becomes a turn in an ongoing chat. The model sees the
user's current state (bands, zip) as part of the system instruction and
the last N turns as chat history, then either replies in plain text or
calls one of the tools in webhook.tools to mutate state.
"""

from __future__ import annotations

import logging

import google.generativeai as genai

from shared import db
from shared.config import config
from webhook import tools

logger = logging.getLogger(__name__)
genai.configure(api_key=config.GEMINI_API_KEY)

MODEL_NAME = "gemini-2.5-flash"
HISTORY_LIMIT = 10

SYSTEM_INSTRUCTION = """You are the assistant for Show List, a concert-alert SMS service.

What the service does:
- Users tell you bands they want to follow. We poll daily and text them when those bands play near their zip code.

How to behave:
- This is SMS. Replies should be one or two short sentences, ideally under 300 characters. Never use markdown.
- Use the tools to read or change the user's tracked bands and zip code. Don't claim you did something unless a tool confirmed it.
- Don't ask for info you already have. If the user's zip is already set, don't ask for it again.
- If a brand-new user just says hi, give a one-sentence pitch and one concrete example ("text a band + zip, like 'Radiohead 90210'").
- If a tool returns ok=false, explain the problem briefly in plain language.
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
        f"- zip code: {user.get('zip') or 'not set'}\n"
        f"- channel: {channel}"
    )


def reply(phone: str, channel: str, message: str) -> str:
    user = db.get_user(phone) or {}
    history = user.get("messages") or []

    model = genai.GenerativeModel(
        MODEL_NAME,
        system_instruction=SYSTEM_INSTRUCTION + _build_profile(user, channel),
        tools=tools.make_tools(phone),
    )

    chat_history = [
        {"role": turn["role"], "parts": [turn["text"]]}
        for turn in history
    ]
    chat = model.start_chat(
        history=chat_history,
        enable_automatic_function_calling=True,
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
