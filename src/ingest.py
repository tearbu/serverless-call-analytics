"""Ingest handler.

Takes a support call transcript and stores the raw record. On AWS it
is triggered by an S3 object-created event: a transcript file lands
in the bucket, this function reads it and writes the record to the
table. Locally the same work happens through ingest_call(), which the
demo script calls directly with the transcript text.

The record written here is deliberately plain: the transcript as
received, plus basic metadata and a status of "raw". Classification,
PII redaction and everything else belong to the analyze step, so this
function stays small and its IAM role only needs S3 read and one
table write.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import get_store  # noqa: E402


def ingest_call(store, call_id, transcript, caller="", channel="phone"):
    """Store one raw call record and return it."""
    record = {
        "call_id": call_id,
        "caller": caller,
        "channel": channel,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "status": "raw",
        "transcript": transcript,
    }
    store.put(record)
    return record


def _read_s3_object(bucket, key):
    import boto3  # provided by the Lambda runtime

    client = boto3.client("s3")
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read().decode("utf-8")


def lambda_handler(event, context=None):
    """Entry point on AWS. Handles an S3 event or a direct payload.

    Direct payload shape: {"call_id": ..., "transcript": ...,
    "caller": ..., "channel": ...}. S3 events are read from the
    standard Records structure and the object key becomes the base
    of the call_id.
    """
    store = get_store()
    created = []

    records = event.get("Records") if isinstance(event, dict) else None
    if records:  # S3 notification
        for item in records:
            bucket = item["s3"]["bucket"]["name"]
            key = item["s3"]["object"]["key"]
            transcript = _read_s3_object(bucket, key)
            call_id = os.path.splitext(os.path.basename(key))[0]
            created.append(
                ingest_call(store, call_id, transcript, channel="upload")
            )
        return {"ingested": [r["call_id"] for r in created]}

    record = ingest_call(
        store,
        event["call_id"],
        event["transcript"],
        caller=event.get("caller", ""),
        channel=event.get("channel", "phone"),
    )
    return {"ingested": [record["call_id"]]}
