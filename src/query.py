"""Query handler.

Read-only access to analyzed calls, exposed on AWS through API
Gateway:

- GET /calls        list of call summaries
- GET /calls/{id}   one call summary
- GET /stats        aggregate counts

Its IAM role only reads the table. Summaries never include the
transcript itself; the redacted text stays in the table for the
support team's own tools.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import get_store  # noqa: E402

SUMMARY_FIELDS = [
    "call_id", "caller", "channel", "received_at", "status",
    "category", "sentiment", "urgency", "pii_flagged",
]


def summarize(record):
    """Trim a stored record down to its summary fields."""
    return {field: record.get(field) for field in SUMMARY_FIELDS}


def aggregate_stats(records):
    """Count analyzed records by category, sentiment, and urgency."""
    stats = {
        "total_calls": 0,
        "by_category": {},
        "by_sentiment": {},
        "by_urgency": {},
        "pii_flagged_count": 0,
    }
    for record in records:
        if record.get("status") != "analyzed":
            continue
        stats["total_calls"] += 1
        for key, field in (
            ("by_category", "category"),
            ("by_sentiment", "sentiment"),
            ("by_urgency", "urgency"),
        ):
            value = record.get(field, "unknown")
            stats[key][value] = stats[key].get(value, 0) + 1
        if record.get("pii_flagged"):
            stats["pii_flagged_count"] += 1
    return stats


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def lambda_handler(event, context=None):
    """Entry point on AWS, behind API Gateway proxy integration."""
    store = get_store()
    path = (event or {}).get("path", "/calls")
    path_params = (event or {}).get("pathParameters") or {}

    if path.rstrip("/").endswith("stats"):
        return _response(200, aggregate_stats(store.all()))

    call_id = path_params.get("callId")
    if call_id:
        record = store.get(call_id)
        if record is None:
            return _response(404, {"error": f"call {call_id} not found"})
        return _response(200, summarize(record))

    summaries = [
        summarize(record)
        for record in store.all()
        if record.get("status") == "analyzed"
    ]
    return _response(200, {"calls": summaries})
