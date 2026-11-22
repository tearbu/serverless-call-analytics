#!/usr/bin/env python3
"""Run the whole pipeline locally, end to end.

Reads every transcript in sample-data/, ingests it into a local
store, analyzes it, then asks the query layer for the aggregate
stats. No AWS account and no installs are needed: the local store
stands in for DynamoDB and the handlers are the same files the SAM
template deploys.

Usage: python3 run_demo.py
"""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from analyze import analyze_record  # noqa: E402
from ingest import ingest_call  # noqa: E402
from query import aggregate_stats, summarize  # noqa: E402
from store import LocalStore  # noqa: E402


def main():
    store = LocalStore()
    sample_files = sorted(glob.glob(os.path.join(ROOT, "sample-data", "*.txt")))
    if not sample_files:
        print("No sample transcripts found in sample-data/")
        return 1

    print(f"Ingesting {len(sample_files)} sample transcripts...")
    for path in sample_files:
        call_id = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as handle:
            transcript = handle.read()
        ingest_call(store, call_id, transcript)
    print("Ingest complete.\n")

    print("Analyzing calls...")
    for record in store.all():
        analyzed = analyze_record(store, record["call_id"])
        summary = summarize(analyzed)
        pii_note = ""
        if summary["pii_flagged"]:
            kinds = ", ".join(analyzed["pii_types"])
            pii_note = f"  PII flagged and redacted ({kinds})"
        print(
            f"  {summary['call_id']}: category={summary['category']}, "
            f"sentiment={summary['sentiment']}, "
            f"urgency={summary['urgency']}{pii_note}"
        )
    print("Analysis complete.\n")

    stats = aggregate_stats(store.all())
    print("Aggregate stats from the query layer:")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
