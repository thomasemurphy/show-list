"""Agentic resolution of a typed band name to a real SeatGeek act.

SeatGeek's own search is literal — every token in the query has to appear in
the performer's name — so shared/seatgeek.py resolves messy input by brute
force: transposing letters, swapping vowels, dropping each word in turn and
re-querying. That works by accident of spelling and knows nothing about music,
which is why "Mumford and Sons" only finds Mumford & Sons via the coincidence
that dropping "and" leaves two words SeatGeek does index, and why a query like
"chase" comes back as three equally-plausible names with nothing to tell them
apart.

This module handles the cases that heuristic can't, by giving a Gemini model
Google Search plus a SeatGeek search tool and letting it work the problem:
look up who the user actually meant, learn how that act officially styles its
name, then hunt for that act's SeatGeek listing under whatever spelling
SeatGeek has it indexed. It asks the user only when two genuinely different
real acts are both plausible — and then with a one-line description of each.

Two guarantees the rest of the system depends on:

  * Every slug and name it returns came from a live SeatGeek search in this
    session (see _decode_finish). The model chooses between acts; it never
    supplies their identifiers, and the name we hand back is SeatGeek's
    canonical spelling, not the model's echo of it.
  * It never fails louder than today's code: any error, timeout, or turn
    exhaustion falls back to seatgeek.resolve_performer_interactive, which is
    exactly the behavior this replaces.

Only for add-time flows, where a user is present to answer a question. The
poller keeps using seatgeek.find_performer.
"""

import logging
import time
from typing import Optional

from google import genai
from google.genai import types

from shared import db, seatgeek
from shared.config import config

logger = logging.getLogger(__name__)

# Gemini 3 is the first family that allows a built-in tool (Google Search) and
# custom function declarations in the same request — 2.5 rejects the
# combination outright — and this loop needs both in the same breath: search
# the web for who the act is, then search SeatGeek for how it's listed.
MODEL_NAME = "gemini-3.8-flash"

# The whole call has to land inside the Rails caller's HTTP timeout (25s), which
# in turn has to beat Heroku's 30s router timeout, so the loop gives up on its
# own terms while there's still time to fall back and answer.
DEADLINE_SECONDS = 20.0

# Enough turns for: search Google, search SeatGeek, retry SeatGeek with the
# official spelling, finish. Anything longer is the model flailing.
MAX_TURNS = 6

# On the last turn — or with less than this much of the deadline left — the
# model is told to stop searching and forced to call finish. Without it, a
# hard query ends in turn exhaustion and falls back to the deterministic
# matcher, which is worse than what the model already knows: asked for
# "radiohead", who currently have no SeatGeek listing, it spends every turn
# hunting while sensibly refusing the tribute acts it keeps finding, and the
# fallback then offers those tribute acts. Forcing the decision gets the
# honest "not on SeatGeek" it was heading toward.
FORCE_FINISH_SECONDS = 6.0

# How many times the model may be corrected (told to decide, or told it made a
# slug up) before we stop paying for turns and fall back.
MAX_CORRECTIONS = 1

MAX_CANDIDATES = 4

SYSTEM_INSTRUCTION = """You identify which musical act a user meant, and match it to a listing on SeatGeek (a ticketing site).

The user typed a band name into a "follow this band" box. It may be misspelled, abbreviated, a nickname, differently punctuated, or a description rather than a name.

How to work:
1. If you don't already know the act with certainty, search Google to identify it and learn how it officially styles its name — "and" vs "&" vs "+", a leading "The" or not, accents and non-Latin characters, numerals vs words.
2. Call search_seatgeek to find that act's listing. SeatGeek's search is literal: every word you pass must appear in the performer's name as SeatGeek spells it, and it does no spelling correction of its own. So try the official stylization, and if that comes back empty or wrong, try plausible variants — the name without "The", the distinctive words alone, the connector written the other way, a corrected spelling.
3. Call finish exactly once, with a slug that came back from search_seatgeek in this conversation. Never invent, guess, or construct a slug.

Choosing:
- Prefer the act that is actually touring: a high event_count and score mean a real, active performer. Tribute acts, cover bands, club nights and theater listings often share a name with the real act — "The Radiohead Trip" is not Radiohead.
- Use genres to tell same-named acts apart.
- Finish "confident" when one act is clearly what the user meant, even if they spelled it differently. Correcting the spelling is the job — don't ask just because the text doesn't match exactly.
- Finish "ambiguous" only when two or more genuinely different real acts are each a plausible reading of what they typed. Give at most 4, each with a description of at most 8 words that would let a fan tell them apart ("Australian alt-R&B trio", "Houston rapper, Travis Scott affiliate"). Ask a short question ("Which Chase?").
- Finish "not_found" when the act isn't on SeatGeek at all, or nothing you found plausibly matches what they typed. Give a one-sentence reason in plain language, addressed to the user — say what you concluded ("I couldn't find a touring act by that name") rather than describing your search."""

_SEARCH_SEATGEEK = types.FunctionDeclaration(
    name="search_seatgeek",
    description=(
        "Search SeatGeek's performer index for a name. Literal matching, no spelling "
        "correction — call it repeatedly with different spellings. Returns performers "
        "with their slug, relevance score, upcoming event_count and genres."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The performer name to search for.",
            ),
        },
        required=["query"],
    ),
)

_FINISH = types.FunctionDeclaration(
    name="finish",
    description="Report the final answer. Call this exactly once, at the end.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                enum=["confident", "ambiguous", "not_found"],
            ),
            "slug": types.Schema(
                type=types.Type.STRING,
                description="status=confident only: the chosen act's SeatGeek slug, "
                            "exactly as search_seatgeek returned it.",
            ),
            "question": types.Schema(
                type=types.Type.STRING,
                description="status=ambiguous only: a short question asking which act "
                            "they meant.",
            ),
            "candidates": types.Schema(
                type=types.Type.ARRAY,
                description="status=ambiguous only: the plausible acts, best first.",
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "slug": types.Schema(
                            type=types.Type.STRING,
                            description="The act's SeatGeek slug, exactly as "
                                        "search_seatgeek returned it.",
                        ),
                        "description": types.Schema(
                            type=types.Type.STRING,
                            description="At most 8 words distinguishing this act from "
                                        "the others.",
                        ),
                    },
                    required=["slug", "description"],
                ),
            ),
            "reason": types.Schema(
                type=types.Type.STRING,
                description="status=not_found only: one plain sentence, addressed to "
                            "the user, on why nothing matched.",
            ),
        },
        required=["status"],
    ),
)

_client_instance: Optional[genai.Client] = None


def _client() -> genai.Client:
    """Lazily built, so the fast path (an exact SeatGeek match) and the fallback
    path never construct a Gemini client at all."""
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client_instance


def resolve(query: str) -> dict:
    """Resolve a typed band name. Returns one of:

      {"status": "confident", "slug":, "name":}
        SeatGeek's canonical name and slug for the act the user meant.
      {"status": "ambiguous", "question":, "candidates": [{slug, name, description}]}
        Several real acts fit; ask the user which. Up to 4.
      {"status": "not_found", "reason":}
        Nothing plausible. reason is a user-facing sentence (possibly empty).

    Same status contract as seatgeek.resolve_performer_interactive, which it
    replaces at every add-time call site, plus the question/description fields
    the deterministic matcher has no way to produce.
    """
    query = (query or "").strip()
    if not query:
        return {"status": "not_found", "reason": ""}

    # An exact (case/punctuation-insensitive) SeatGeek name match needs no
    # judgment — "Radiohead" is Radiohead. Skip the model entirely: it's the
    # common case, and it keeps a normal add at a couple hundred milliseconds.
    exact = seatgeek.exact_match(query)
    if exact:
        logger.info("[resolver] %r → %r (exact SeatGeek match, no LLM)", query, exact["slug"])
        return {"status": "confident", "slug": exact["slug"], "name": exact["name"]}

    cached = _cached_alias(query)
    if cached:
        logger.info("[resolver] %r → %r (cached alias)", query, cached["slug"])
        return cached

    started = time.monotonic()
    try:
        result = _agent_resolve(query)
    except Exception:
        logger.exception("[resolver] agent loop failed for %r", query)
        result = None

    if result is None:
        logger.info("[resolver] %r → falling back to deterministic matching", query)
        return _normalize(seatgeek.resolve_performer_interactive(query), query)

    logger.info("[resolver] %r → %s in %.1fs", query, result["status"], time.monotonic() - started)
    if result["status"] == "confident":
        _cache_alias(query, result["slug"], result["name"])
    return result


def _normalize(result: dict, query: str) -> dict:
    """Give a deterministic seatgeek result the extra fields the agent path
    produces (a question, a description per candidate), so callers see one
    shape whichever path answered. There's nothing to describe candidates with
    down here — the fallback only knows names and scores — so the descriptions
    come back empty and the UI simply omits them.
    """
    if result.get("status") != "ambiguous":
        return result
    return {
        "status": "ambiguous",
        "question": f'Which "{query}"?',
        "candidates": [
            {"slug": c["slug"], "name": c["name"], "description": ""}
            for c in result.get("candidates") or []
        ],
    }


def _config(force_finish: bool) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(function_declarations=[_SEARCH_SEATGEEK, _FINISH]),
        ],
        tool_config=types.ToolConfig(
            # Required whenever a built-in tool and function declarations are
            # used together: the API returns Google Search's own calls and
            # results as parts in the model's content, and refuses the request
            # outright without this. We hand those parts straight back on the
            # next turn, so the model keeps what it read.
            include_server_side_tool_invocations=True,
            # On a forced turn, finish is the only call it may make.
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=["finish"],
            ) if force_finish else None,
        ),
        # Manual loop, unlike webhook/conversation.py's automatic-FC chat: this
        # one needs a turn cap, a deadline, and a say in what "done" means.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def _agent_resolve(query: str) -> Optional[dict]:
    """Run the tool loop. Returns a resolve() result, or None to fall back."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    corrections = 0
    # Every performer any search_seatgeek call has returned, slug → SeatGeek's
    # own spelling of the name. This is both the whitelist the model's answer is
    # checked against and where the canonical name comes from.
    seen = {}
    contents = [types.Content(
        role="user",
        parts=[types.Part(text=f'The user typed: "{query}"')],
    )]
    for turn in range(MAX_TURNS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info("[resolver] deadline hit for %r", query)
            return None

        # Out of turns or nearly out of time: make this one the decision.
        force_finish = turn == MAX_TURNS - 1 or remaining < FORCE_FINISH_SECONDS
        if force_finish:
            contents.append(types.Content(role="user", parts=[types.Part(
                text="No more searching — call finish now with what you have. If none "
                     "of the acts you found is plausibly the one they meant, finish "
                     "with not_found."
            )]))

        response = _client().models.generate_content(
            model=MODEL_NAME, contents=contents, config=_config(force_finish)
        )
        if not response.candidates or not response.candidates[0].content:
            return None
        # Appended verbatim — Gemini 3 carries thought signatures on these parts
        # and rejects a follow-up turn that drops them.
        contents.append(response.candidates[0].content)

        calls = response.function_calls or []
        if not calls:
            # Answered in prose instead of calling finish. Worth one nudge.
            corrections += 1
            if corrections > MAX_CORRECTIONS:
                return None
            contents.append(types.Content(role="user", parts=[types.Part(
                text="Decide now and report it by calling the finish function."
            )]))
            continue

        parts = []
        for call in calls:
            if call.name == "finish":
                result = _decode_finish(call.args or {}, seen, query)
                if result:
                    return result
                corrections += 1
                if corrections > MAX_CORRECTIONS:
                    logger.info("[resolver] unusable finish for %r: %s", query, call.args)
                    return None
                parts.append(types.Part.from_function_response(
                    name="finish",
                    response={"error": "Every slug must be one that search_seatgeek "
                                       "returned in this conversation. Search for the "
                                       "act, then call finish with a slug from the "
                                       "results."},
                ))
            elif call.name == "search_seatgeek":
                sub_query = (call.args or {}).get("query") or query
                results = seatgeek.search_candidates(sub_query)
                logger.info("[resolver] search_seatgeek(%r) → %s",
                            sub_query, [r["name"] for r in results])
                for r in results:
                    if r["slug"]:
                        seen[r["slug"]] = r["name"]
                parts.append(types.Part.from_function_response(
                    name="search_seatgeek", response={"results": results},
                ))
            else:
                parts.append(types.Part.from_function_response(
                    name=call.name, response={"error": f"No such tool: {call.name}"},
                ))
        contents.append(types.Content(role="user", parts=parts))

    return None


def _decode_finish(args: dict, seen: dict, query: str) -> Optional[dict]:
    """Turn a finish call into a resolve() result, or None if it can't be
    trusted — a slug the model made up rather than one search_seatgeek actually
    returned. Names come from `seen` (SeatGeek's spelling), never from the
    model's echo of them, so what we store is what SeatGeek will match later.
    """
    status = args.get("status")

    if status == "confident":
        slug = args.get("slug")
        if slug not in seen:
            return None
        return {"status": "confident", "slug": slug, "name": seen[slug]}

    if status == "ambiguous":
        candidates = []
        for c in args.get("candidates") or []:
            slug = c.get("slug")
            if slug in seen and not any(x["slug"] == slug for x in candidates):
                candidates.append({
                    "slug": slug,
                    "name": seen[slug],
                    "description": (c.get("description") or "").strip(),
                })
        if not candidates:
            return None
        return {
            "status": "ambiguous",
            "question": (args.get("question") or "").strip() or f'Which "{query}"?',
            "candidates": candidates[:MAX_CANDIDATES],
        }

    return {"status": "not_found", "reason": (args.get("reason") or "").strip()}


def _cached_alias(query: str) -> Optional[dict]:
    try:
        cached = db.get_band_alias(query)
    except Exception:
        logger.warning("[resolver] alias cache read failed for %r", query, exc_info=True)
        return None
    if cached and cached.get("slug") and cached.get("name"):
        return {"status": "confident", "slug": cached["slug"], "name": cached["name"]}
    return None


def _cache_alias(query: str, slug: str, name: str) -> None:
    try:
        db.set_band_alias(query, slug, name)
    except Exception:
        logger.warning("[resolver] alias cache write failed for %r", query, exc_info=True)
