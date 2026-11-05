"""Pre-model safety check for the chat assistant.

A visitor who tells the astronomy assistant they are thinking of suicide or
self-harm should get a caring, fixed reply that points to real help -- not a
retrieval over supernova documents, and not a flat "I don't have information
about that".

This runs BEFORE any embedding or model call, so a match costs nothing, sends
nothing to Google, and is deterministic. It is deliberately conservative: it
looks for first-person intent ("I want to kill myself"), not topic words, so
questions about supernovae, stellar death or "what kills a white dwarf" never
trigger it. It cannot catch every paraphrase; the answer prompt carries the same
rule as a second layer.

Privacy: this module never logs, stores or returns the text it matched. Keep it
that way -- do not add logging here or around its call sites.

No third-party imports, so it works (and is tested) without the RAG stack.
"""

import re
from typing import Optional

SELF_HARM_MESSAGE = (
    "I'm really sorry you're hurting. I'm an astronomy assistant, so I can't help "
    "with this here, but you deserve real support right now. If you're in Canada or "
    "the US, you can call or text 988 at any time to talk with someone. If you're in "
    "immediate danger, call 911 or your local emergency number. Anywhere else, "
    "findahelpline.com lists services by country. If you can, reach out to someone "
    "you trust and tell them how you're feeling."
)

# Up to this many characters between an intent phrase and the harm phrase.
_GAP = r"[^.?!\n]{0,30}?"

_INTENT = (
    r"(?:want(?:ed)?|wanna|going|gonna|plan(?:ning|ned)?|thinking\s+(?:about|of)|"
    r"ready|tempted|urge|decided|can'?t\s+stop)"
)
_HARM_TO_SELF = (
    r"(?:kill(?:ing)?|hurt(?:ing)?|harm(?:ing)?|cut(?:ting)?|injur(?:e|ing)|"
    r"end(?:ing)?)\s+my\s?self"
)

# Words that turn an intent phrase into its opposite ("careful not to hurt myself").
_NEGATION = re.compile(r"\b(?:not|n't|never|avoid|careful|without|safe|safely|prevent)\b")

# (pattern, check_negation). Patterns that join an intent word to a harm phrase
# across a gap are checked for negation; the unambiguous ones are not.
_SPECS = (
    # "I want to kill myself", "I'm going to hurt myself", "I decided to end myself"
    (rf"\b(?:i|im|ive)\b{_GAP}\b{_INTENT}\b{_GAP}\b{_HARM_TO_SELF}\b", True),
    # "I have the urge to cut myself"
    (rf"\b(?:i|im|ive)\b{_GAP}\b(?:urge|thoughts?)\s+(?:to|of)\s+{_HARM_TO_SELF}\b", True),
    # Unambiguous on their own.
    (r"\bkill(?:ing)?\s+my\s?self\b", False),
    (r"\bend(?:ing)?\s+my\s+(?:own\s+)?life\b", False),
    (r"\btak(?:e|ing)\s+my\s+(?:own\s+)?life\b", False),
    (r"\bcommit(?:ting)?\s+suicide\b", False),
    # First-person suicidal state or thoughts.
    (r"\b(?:i|im|ive)(?:'m|\s+am|'ve|\s+have)?\s*(?:been\s+)?"
     r"(?:really\s+|so\s+|very\s+|feel(?:ing)?\s+)*suicidal\b", False),
    (r"\bi\b[^.?!\n]{0,20}\b(?:think(?:ing)?|thoughts?)\s+(?:about|of)\s+"
     r"(?:suicide|killing\s+my\s?self|ending\s+(?:my\s+life|it\s+all))\b", False),
    # Wanting to be dead.
    (r"\bi(?:'m|\s+am)?\s+(?:really\s+|just\s+|so\s+)?"
     r"(?:want(?:ed)?|wanna|ready|planning|wish)\s+(?:to\s+)?(?:die|be\s+dead)\b", False),
    (r"\bi\s+wish\s+i\s+(?:was|were)\s+(?:dead|never\s+born)\b", False),
    (r"\bi\s+wish\s+i\s+(?:could\s+)?(?:just\s+)?(?:die|disappear\s+forever)\b", False),
    (r"\b(?:no\s+reason\s+to\s+(?:live|go\s+on)|better\s+off\s+dead)\b", False),
    (r"\b(?:don'?t|do\s+not|no\s+longer)\s+want\s+to\s+(?:live|be\s+alive)\b", False),
    (r"\beveryone\s+(?:would\s+be|'?d\s+be)\s+better\s+off\s+without\s+me\b", False),
    # Ongoing self-harm.
    (r"\bi\s+(?:keep|have\s+been|'?ve\s+been|started|am)\s+"
     r"(?:cutting|hurting|harming)\s+my\s?self\b", False),
)
_PATTERNS = [(re.compile(pattern), check_negation) for pattern, check_negation in _SPECS]


def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip()


def is_self_harm(text: str) -> bool:
    """True if ``text`` expresses first-person suicide or self-harm intent."""
    normalised = _normalise(text)
    for pattern, check_negation in _PATTERNS:
        match = pattern.search(normalised)
        if match and not (check_negation and _NEGATION.search(match.group(0))):
            return True
    return False


def conversation_is_self_harm(question: str, history: Optional[list[dict]] = None) -> bool:
    """Check the new question and every user turn in the provided history.

    Only user turns are inspected: an assistant turn is our own text.
    """
    if is_self_harm(question):
        return True
    return any(
        turn.get("role") == "user" and is_self_harm(turn.get("content", ""))
        for turn in (history or [])
    )


def guarded_response(question: str, history: Optional[list[dict]] = None) -> Optional[dict]:
    """The fixed reply, in the normal ``{answer, sources}`` shape, or None.

    Returns None for an ordinary question. Never logs or returns the matched text.
    """
    if conversation_is_self_harm(question, history):
        return {"answer": SELF_HARM_MESSAGE, "sources": []}
    return None
