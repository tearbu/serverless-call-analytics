"""Analyze handler.

Classifies one stored call record:

- category: billing, outage, access, technical, or other, picked by
  keyword scoring against fixed word lists.
- sentiment: positive, negative, or neutral, from a simple count of
  positive and negative words. Deterministic on purpose: the same
  transcript always scores the same way, which makes it testable.
- urgency: high when the caller uses urgency language, medium when
  the sentiment is negative, otherwise low.
- PII: the transcript is scanned for card numbers (with a Luhn
  check, so random digit strings do not flag), US Social Security
  numbers, phone numbers, and email addresses. Matches are replaced
  with redaction labels and the analyzed record stores the redacted
  text, never the original values. The record keeps only the fact
  that PII was found and which kinds.

On AWS this runs when a new record appears in the table (DynamoDB
Streams). The deployed upgrade path swaps analyze_transcript() for a
managed AI call (Amazon Bedrock or Comprehend) behind this same
interface; see ARCHITECTURE.md.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import get_store  # noqa: E402

CATEGORY_KEYWORDS = {
    "billing": [
        "bill", "billing", "billed", "charge", "charged", "invoice",
        "refund", "payment", "overcharged", "subscription fee",
        "price increase",
    ],
    "outage": [
        "outage", "no service", "no signal", "blackout", "went down",
        "is down", "offline", "power cut", "service is out",
    ],
    "access": [
        "password", "log in", "login", "locked out", "locked",
        "sign in", "reset my", "two-factor", "authentication",
        "access my account", "username",
    ],
    "technical": [
        "router", "modem", "error", "not working", "blinking",
        "slow speeds", "device", "firmware", "crash", "freezes",
        "update failed", "screen",
    ],
}

# Fixed order so ties resolve the same way every run.
CATEGORY_ORDER = ["billing", "outage", "access", "technical"]

POSITIVE_WORDS = [
    "thank", "thanks", "great", "happy", "resolved", "excellent",
    "appreciate", "helpful", "satisfied", "perfect", "pleased",
    "wonderful", "good news",
]

NEGATIVE_WORDS = [
    "angry", "frustrated", "frustrating", "terrible", "awful",
    "unacceptable", "worst", "disappointed", "furious", "ridiculous",
    "fed up", "useless", "horrible",
]

URGENCY_WORDS = [
    "urgent", "immediately", "emergency", "asap", "right now",
    "critical", "cannot work", "can't work", "losing money",
    "deadline",
]

CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}-\d{4}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _count_hits(text, phrases):
    hits = 0
    for phrase in phrases:
        hits += text.count(phrase)
    return hits


def classify_category(text):
    """Return the category with the most keyword hits, or 'other'."""
    best_category = "other"
    best_score = 0
    for category in CATEGORY_ORDER:
        score = _count_hits(text, CATEGORY_KEYWORDS[category])
        if score > best_score:
            best_category = category
            best_score = score
    return best_category


def score_sentiment(text):
    """Return (label, score) from positive and negative word counts."""
    positive = _count_hits(text, POSITIVE_WORDS)
    negative = _count_hits(text, NEGATIVE_WORDS)
    score = positive - negative
    if score > 0:
        return "positive", score
    if score < 0:
        return "negative", score
    return "neutral", score


def assess_urgency(text, sentiment_label):
    if _count_hits(text, URGENCY_WORDS) > 0:
        return "high"
    if sentiment_label == "negative":
        return "medium"
    return "low"


def _luhn_ok(digits):
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _is_card_number(candidate):
    digits = re.sub(r"\D", "", candidate)
    return 13 <= len(digits) <= 16 and _luhn_ok(digits)


def redact_pii(text):
    """Replace PII patterns with labels.

    Returns (redacted_text, pii_types) where pii_types lists each kind
    found, in the order the checks run: card, ssn, phone, email.
    """
    found = []

    def card_sub(match):
        if _is_card_number(match.group(0)):
            if "card" not in found:
                found.append("card")
            return "[REDACTED-CARD]"
        return match.group(0)

    def labelled(kind, label):
        def sub(match):
            if kind not in found:
                found.append(kind)
            return label

        return sub

    text = CARD_RE.sub(card_sub, text)
    text = SSN_RE.sub(labelled("ssn", "[REDACTED-SSN]"), text)
    text = PHONE_RE.sub(labelled("phone", "[REDACTED-PHONE]"), text)
    text = EMAIL_RE.sub(labelled("email", "[REDACTED-EMAIL]"), text)
    return text, found


def analyze_transcript(transcript):
    """Run every check over one transcript and return the analysis."""
    lowered = transcript.lower()
    category = classify_category(lowered)
    sentiment, sentiment_score = score_sentiment(lowered)
    urgency = assess_urgency(lowered, sentiment)
    redacted_text, pii_types = redact_pii(transcript)
    return {
        "category": category,
        "sentiment": sentiment,
        "sentiment_score": sentiment_score,
        "urgency": urgency,
        "pii_flagged": bool(pii_types),
        "pii_types": pii_types,
        "redacted_transcript": redacted_text,
    }


def analyze_record(store, call_id):
    """Analyze one stored record and write the result back."""
    record = store.get(call_id)
    if record is None:
        raise KeyError(f"no stored record for call_id {call_id!r}")
    result = analyze_transcript(record["transcript"])
    record["category"] = result["category"]
    record["sentiment"] = result["sentiment"]
    record["sentiment_score"] = result["sentiment_score"]
    record["urgency"] = result["urgency"]
    record["pii_flagged"] = result["pii_flagged"]
    record["pii_types"] = result["pii_types"]
    # The stored transcript is replaced by the redacted version, so
    # PII values never persist in the results table.
    record["transcript"] = result["redacted_transcript"]
    record["status"] = "analyzed"
    store.put(record)
    return record


def lambda_handler(event, context=None):
    """Entry point on AWS.

    Accepts a DynamoDB Streams event (the deployed trigger) or a
    direct payload of {"call_id": ...} or {"call_ids": [...]}.
    """
    store = get_store()
    call_ids = []
    if isinstance(event, dict) and event.get("Records"):
        for item in event["Records"]:
            image = item.get("dynamodb", {}).get("NewImage", {})
            if "call_id" in image:
                call_ids.append(image["call_id"]["S"])
    elif isinstance(event, dict) and "call_ids" in event:
        call_ids = list(event["call_ids"])
    elif isinstance(event, dict) and "call_id" in event:
        call_ids = [event["call_id"]]

    analyzed = [analyze_record(store, call_id) for call_id in call_ids]
    return {"analyzed": [r["call_id"] for r in analyzed]}
