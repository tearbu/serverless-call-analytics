"""Tests for the query layer: aggregation and the pipeline round trip."""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)

from analyze import analyze_record  # noqa: E402
from ingest import ingest_call  # noqa: E402
from query import aggregate_stats, summarize  # noqa: E402
from store import LocalStore  # noqa: E402


def make_record(call_id, **fields):
    record = {
        "call_id": call_id,
        "caller": "",
        "channel": "phone",
        "received_at": "2026-11-22T00:00:00+00:00",
        "status": "analyzed",
        "category": "other",
        "sentiment": "neutral",
        "urgency": "low",
        "pii_flagged": False,
        "pii_types": [],
        "transcript": "",
    }
    record.update(fields)
    return record


class AggregateStatsTests(unittest.TestCase):
    def test_counts_by_category_sentiment_and_pii(self):
        records = [
            make_record("a", category="billing", sentiment="negative",
                        urgency="medium"),
            make_record("b", category="billing", sentiment="negative",
                        urgency="medium", pii_flagged=True),
            make_record("c", category="outage", sentiment="positive",
                        urgency="high"),
        ]
        stats = aggregate_stats(records)
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["by_category"], {"billing": 2, "outage": 1})
        self.assertEqual(stats["by_sentiment"], {"negative": 2, "positive": 1})
        self.assertEqual(stats["by_urgency"], {"medium": 2, "high": 1})
        self.assertEqual(stats["pii_flagged_count"], 1)

    def test_raw_records_are_not_counted(self):
        records = [
            make_record("a", category="billing"),
            make_record("b", status="raw"),
        ]
        stats = aggregate_stats(records)
        self.assertEqual(stats["total_calls"], 1)

    def test_empty_input_gives_zeroes(self):
        stats = aggregate_stats([])
        self.assertEqual(stats["total_calls"], 0)
        self.assertEqual(stats["pii_flagged_count"], 0)


class PipelineRoundTripTests(unittest.TestCase):
    def test_ingest_analyze_query_round_trip(self):
        store = LocalStore()
        ingest_call(
            store,
            "call-1",
            "I was overcharged on my bill. My card is 4111 1111 1111 1111.",
            caller="Test Caller",
        )
        record = analyze_record(store, "call-1")
        self.assertEqual(record["status"], "analyzed")
        self.assertEqual(record["category"], "billing")
        self.assertTrue(record["pii_flagged"])
        # The stored transcript holds the redacted text only.
        self.assertNotIn("4111", record["transcript"])
        summary = summarize(record)
        self.assertNotIn("transcript", summary)
        stats = aggregate_stats(store.all())
        self.assertEqual(stats["total_calls"], 1)
        self.assertEqual(stats["pii_flagged_count"], 1)


if __name__ == "__main__":
    unittest.main()
