"""Storage layer for the call analytics pipeline.

Every handler talks to storage through this small interface only:
put(record), get(call_id), all(). Two implementations sit behind it.

LocalStore keeps records in memory, with an optional JSON file so a
demo run can be inspected afterwards. It stands in for DynamoDB when
the pipeline runs on a laptop.

DynamoStore is the real thing. It imports boto3 lazily, inside the
constructor, because boto3 ships with the Lambda runtime on AWS but
is not needed (and not installed) for local runs. The rest of the
code never imports boto3 at all.

get_store() picks the implementation from the environment: when the
CALLS_TABLE variable is set (the SAM template sets it on every
function) the DynamoDB store is used, otherwise the local one.
"""

import json
import os


class LocalStore:
    """In-memory record store with optional JSON file persistence."""

    def __init__(self, path=None):
        self._path = path
        self._records = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            for record in saved:
                self._records[record["call_id"]] = record

    def _flush(self):
        if self._path:
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self.all(), handle, indent=2)

    def put(self, record):
        self._records[record["call_id"]] = record
        self._flush()

    def get(self, call_id):
        return self._records.get(call_id)

    def all(self):
        return [self._records[key] for key in sorted(self._records)]


class DynamoStore:
    """DynamoDB-backed store, used when running on AWS."""

    def __init__(self, table_name=None):
        import boto3  # provided by the Lambda runtime, not needed locally

        name = table_name or os.environ["CALLS_TABLE"]
        self._table = boto3.resource("dynamodb").Table(name)

    def put(self, record):
        self._table.put_item(Item=record)

    def get(self, call_id):
        response = self._table.get_item(Key={"call_id": call_id})
        return response.get("Item")

    def all(self):
        items = []
        response = self._table.scan()
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = self._table.scan(
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            items.extend(response.get("Items", []))
        return items


def get_store():
    """Return the store the current environment calls for."""
    if os.environ.get("CALLS_TABLE"):
        return DynamoStore()
    return LocalStore(os.environ.get("LOCAL_STORE_PATH"))
