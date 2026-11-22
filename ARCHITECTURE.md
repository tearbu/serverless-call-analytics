# Architecture

## The flow

A support call ends, its transcript is saved as a text file, and the
platform answers three questions about it without anyone opening the
file: what was the call about, how did the caller feel, and did the
transcript contain personal data that should not sit in a database.

```
 transcript .txt
       |
       v
 +-----------+      put raw record     +----------------+
 | S3 bucket | ----------------------> |  Ingest Lambda |
 +-----------+   (object created)      +-------+--------+
                                               |
                                               v
                                       +----------------+
                                       | DynamoDB table |
                                       +---+--------+---+
                                           |        |
                              stream       |        |  read
                              (INSERT)     |        |
                                           v        |
                                  +----------------+|
                                  | Analyze Lambda ||
                                  | classify, score,||
                                  | redact PII      ||
                                  +--------+--------+|
                                           | writes analysis
                                           v         v
                                       +----------------+
                                       |  Query Lambda  |
                                       +--------+-------+
                                                |
                                          API Gateway
                                     GET /calls, /calls/{id},
                                               /stats
```

## What each part does

**S3 bucket.** The landing zone. A transcript file arriving here is
the event that starts everything. Files are plain text with a .txt
suffix, which the ingest trigger filters on.

**Ingest Lambda** (`src/ingest.py`). Reads the new object and stores
a raw record: the transcript as received, the call id taken from the
file name, a timestamp, and status "raw". It does no analysis. A
small function with a small job keeps its IAM role small too: S3 read
on the bucket, one PutItem on the table.

**DynamoDB table.** One item per call, keyed by call_id. Streams are
turned on with the NEW_IMAGE view, so every new record announces
itself to the analyze step. Billing is on-demand; see Decisions.

**Analyze Lambda** (`src/analyze.py`). Triggered by the table stream
on INSERT events. It classifies the call into a category (billing,
outage, access, technical, other) by keyword scoring, scores
sentiment from fixed positive and negative word lists, rates urgency,
and runs the PII pass. Card numbers must pass a Luhn check before
they count, so a random reference number does not raise a false
alarm. Matches are replaced with labels like [REDACTED-CARD], and
the record in the table is overwritten with the redacted transcript
plus the analysis fields and status "analyzed". The original PII
values are never written back.

**Query Lambda** (`src/query.py`). The read side, behind API
Gateway. GET /calls lists summaries (no transcript text), GET
/calls/{id} returns one summary, and GET /stats returns the
aggregate counts: totals by category, sentiment, and urgency, plus
how many calls had PII flagged. Its role can only read the table.

**Store layer** (`src/store.py`). The handlers never touch boto3
directly. They call put, get, and all on a store object. On AWS that
object is DynamoStore; locally it is LocalStore, an in-memory stand
in with optional JSON file output. That single seam is what lets the
same handler code run in the local demo and in the SAM template.

## The record

| Field | Set by | Notes |
| --- | --- | --- |
| call_id | ingest | from the transcript file name |
| caller, channel | ingest | metadata, empty when unknown |
| received_at | ingest | UTC timestamp |
| status | ingest, analyze | "raw", then "analyzed" |
| transcript | ingest, analyze | replaced by the redacted text during analysis |
| category, sentiment, sentiment_score, urgency | analyze | from the rule-based analyzer |
| pii_flagged, pii_types | analyze | kinds found, never the values |

## Decisions

**SAM over Terraform or CDK for this project.** The stack is three
functions, a bucket, a table, and an API, all in one account. SAM
describes that in one short file, wires the S3, stream, and API
events without extra glue resources, and deploys with two commands.
Terraform would earn its keep with multi-account state or a mix of
clouds; neither applies here, and its state file is one more thing
to protect. CDK is a good option too, but it adds a build step and a
library surface to a project whose point is that the infrastructure
stays readable next to the code.

**DynamoDB on-demand.** Call volume in this design is spiky and
unknown in advance: quiet hours, then a burst when an outage hits.
On-demand billing follows that shape and removes capacity planning
from a portfolio-scale project. Provisioned capacity with autoscaling
would be the change to make if traffic became steady and large.

**A rule-based analyzer locally, managed AI as the deployed upgrade.**
The local analyzer is deterministic: fixed word lists, fixed scoring,
same input gives same output. That makes it testable, and the tests
in this repo lean on exactly that property. It is also honest about
its limits; keyword scoring misses sarcasm, context, and anything
said in other words. The deployed upgrade path keeps the same
interface, analyze_transcript(text) returning the same fields, and
swaps the internals for a managed service: Amazon Bedrock for
category and summary quality, or Amazon Comprehend for sentiment and
its built-in PII detection. The interface was kept narrow on purpose
so that swap touches one function.

**One IAM role per function, scoped to its job.** Ingest can read
the bucket and put one kind of item. Analyze can read and update
records and read the stream. Query can only read. If the query API
were compromised, the role behind it cannot write or delete
anything. The template writes these as explicit policy statements
rather than broad managed policies, so the permissions can be read
at a glance.

**Redaction inside the analyze step.** The raw transcript exists in
the table only between ingest and analysis, a window the stream
trigger keeps short. After analysis, the stored transcript is the
redacted version and the record carries only which kinds of PII were
found. A stricter design would redact in ingest before the first
write; that is the first change to make before this pattern touches
real calls.
