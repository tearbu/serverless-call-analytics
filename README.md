# Serverless Call Analytics

## Story

Every support call ends with a transcript sitting in a file. Most
teams never look at those files again, which means the patterns
inside them stay invisible: how many calls were really about billing,
which ones left the caller angry, and which transcripts quietly
contain a card number or a Social Security number that someone read
out loud and an agent typed into the notes.

This project is a small serverless platform that reads those
transcripts and answers those questions. A transcript lands in an S3
bucket, two Lambda functions process it, the results live in
DynamoDB, and an API serves the summaries and the aggregate counts.
It is a portfolio project, built to show the full shape of the
system: event-driven processing, infrastructure as code, scoped IAM
roles, automated tests, and a PII check that treats a support
transcript as the sensitive document it is.

## How it works

The pipeline has four steps, and each one is a separate piece with a
separate job:

1. **Ingest.** A transcript file (.txt) lands in the S3 bucket. The
   ingest Lambda reads it and stores a raw record.
2. **Analyze.** The new record fires a DynamoDB stream event. The
   analyze Lambda classifies the call, scores sentiment, rates
   urgency, and runs the PII pass. Card numbers have to pass a Luhn
   check before they count. Anything found is replaced with a label
   such as [REDACTED-CARD], and the record is stored with the
   redacted text, so the original values never persist.
3. **Store.** One DynamoDB item per call holds the metadata, the
   redacted transcript, and the analysis fields.
4. **Query.** A third Lambda behind API Gateway serves call
   summaries and aggregate stats: counts by category, the sentiment
   split, and how many calls had PII flagged.

The handlers never call AWS directly. They talk to a small store
layer (`src/store.py`) with two implementations: DynamoDB on AWS,
and a local in-memory store for the demo and the tests. That one seam
is why the same handler files run in both places.

The analyzer sorts each call into one of five categories (billing,
outage, access, technical, other) by keyword scoring, and rates
sentiment by counting words from fixed positive and negative lists.
It is deterministic: the same transcript always gets the same result,
which is what makes it testable.

Full detail, including an ASCII diagram and the record layout, is in
[ARCHITECTURE.md](ARCHITECTURE.md).

### Decisions

- **SAM for the infrastructure**, because the stack is one account
  and a handful of resources, and SAM wires the S3, stream, and API
  events in one short file. The reasoning, including why not
  Terraform or CDK here, is in ARCHITECTURE.md.
- **DynamoDB on-demand**, because call volume is bursty and unknown,
  and on-demand billing follows that shape without capacity planning.
- **A rule-based analyzer in this version.** Deterministic scoring is
  testable and honest about its limits. The deployed upgrade path
  keeps the same analyze_transcript interface and swaps the internals
  for a managed AI call, Amazon Bedrock for classification or Amazon
  Comprehend for sentiment and PII. That swap is designed, not wired
  to a live service.
- **One IAM role per function, scoped to its job.** Ingest reads S3
  and writes the table. Analyze reads and writes the table. Query
  only reads. No function can delete anything.
- **Python standard library only** for the handlers and the demo, so
  the local run needs no installs at all.

## Output

Run the local simulation. It ingests all seven sample transcripts
from `sample-data/`, analyzes them, and prints the aggregate stats
from the query layer:

```bash
python3 run_demo.py
```

Real output from that run:

```
Ingesting 7 sample transcripts...
Ingest complete.

Analyzing calls...
  001-billing-overcharge: category=billing, sentiment=negative, urgency=medium
  002-outage-neighborhood: category=outage, sentiment=negative, urgency=high
  003-account-locked: category=access, sentiment=neutral, urgency=low
  004-router-blinking: category=technical, sentiment=neutral, urgency=low
  005-happy-referral: category=other, sentiment=positive, urgency=low
  006-billing-card-update: category=billing, sentiment=negative, urgency=medium  PII flagged and redacted (card, ssn, phone, email)
  007-technical-resolved: category=technical, sentiment=positive, urgency=low
Analysis complete.

Aggregate stats from the query layer:
{
  "total_calls": 7,
  "by_category": {
    "billing": 2,
    "outage": 1,
    "access": 1,
    "technical": 2,
    "other": 1
  },
  "by_sentiment": {
    "negative": 3,
    "neutral": 2,
    "positive": 2
  },
  "by_urgency": {
    "medium": 2,
    "high": 1,
    "low": 4
  },
  "pii_flagged_count": 1
}
```

The sample transcripts are fictional. Names, companies, and the card
and Social Security numbers in file 006 are invented test values,
written so the PII checker has something real to catch.

Tests, run with the standard library test runner:

```bash
python3 -m unittest discover -s tests
```

Result: 22 tests, all passing. They cover category classification,
sentiment scoring, urgency, PII detection and redaction (including a
digit string that fails the Luhn check and must not flag), the query
aggregation, and a full ingest to analyze to query round trip.

The SAM template can be checked locally:

```bash
python3 validate_template.py
```

That script parses `template.yaml` with PyYAML (using a loader that
tolerates the SAM intrinsic tags) and asserts the resource structure:
all five resources with the right types, the table on-demand with a
NEW_IMAGE stream, and the IAM action lists per function, including
that the query function is read-only. It passed. What it does not
do: it is not `sam validate` and not a deployment, so CloudFormation
could still reject something this check cannot see. The live run
below proved that warning right, twice.

### Live deployment

On 23 November 2026 this project was deployed to a real AWS account
and run end to end. Region us-east-1, stack
serverless-call-analytics, status CREATE_COMPLETE, deployed from a
CloudShell clone of this repo with `aws cloudformation package` and
`aws cloudformation deploy`. The full step-by-step record, including
two failed attempts and the template defects behind them, is in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Stack outputs:

| Output | Value |
| --- | --- |
| BucketName | serverless-call-analytics-transcripts |
| TableName | serverless-call-analytics-calls |
| ApiUrl | https://pb6eii1y8k.execute-api.us-east-1.amazonaws.com/Prod |

All seven sample transcripts were uploaded to the bucket. The S3
event triggered ingest and the DynamoDB stream triggered analyze,
with no manual invocations. A scan of the table showed 7 items, all
analyzed: billing 2, technical 2, access 1, outage 1, other 1;
sentiment negative 3, neutral 2, positive 2; urgency high 1, medium
2, low 4; one call with PII flagged and redacted (transcript 006,
card, ssn, phone, email). The same numbers as the local run above.

The stats endpoint answered live:

```
GET https://pb6eii1y8k.execute-api.us-east-1.amazonaws.com/Prod/calls/stats
```

```json
{"total_calls":7,"by_category":{"access":1,"billing":2,"other":1,"technical":2,"outage":1},"by_sentiment":{"neutral":2,"negative":3,"positive":2},"by_urgency":{"low":4,"medium":2,"high":1},"pii_flagged_count":1}
```

`GET /calls` on the same base URL returned the analyzed records.
The stack held only the fictional sample data. Later the same day,
at the owner's direction, it was torn down to avoid ongoing
charges: the transcripts bucket was emptied and removed with the
stack, and the separate artifacts bucket was emptied and deleted.
The API endpoint above is no longer active. The outputs and the
response reproduced here are the record of that run. To run it
again, use the commands in the Deploying section below.

## Deploying to AWS

The deployment above used the CloudFormation commands from
CloudShell, recorded step by step in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md):

```bash
aws cloudformation package --template-file template.yaml \
  --s3-bucket <artifacts-bucket> --output-template-file packaged.yaml
aws cloudformation deploy --template-file packaged.yaml \
  --stack-name serverless-call-analytics --capabilities CAPABILITY_IAM
```

After deployment, upload a transcript to the bucket from the stack
outputs and read the results at the API URL from the same outputs
(GET /calls and GET /calls/stats).

## Worth

What this project shows, in practical terms:

- An event-driven pipeline with no servers to patch or scale, where
  each function has one job and an IAM role to match.
- Infrastructure written as code and checked by a script, not
  clicked together in a console.
- A test suite that pins down the analyzer's behavior, including the
  awkward cases, like a reference number that looks like a card
  number but is not.
- PII handling treated as a design question, with redaction before
  the results settle in storage and only the kinds of PII recorded,
  never the values.

Honest limits:

- **Deployed and verified live on 23 November 2026 in us-east-1,
  then torn down the same day.** The pipeline ran end to end on real
  AWS services with the seven fictional transcripts, and the API
  answered at the URL in the Output section during that run. At the
  owner's direction the stack was deleted the same day to avoid
  ongoing charges, so nothing from this project is currently
  running; the outputs recorded in the Output section are the
  evidence of the run. The deployment also exposed two defects in
  the template as first published: a DynamoDB stream event property the SAM
  transform rejects, and an IAM policy reference that created a
  circular dependency. Both are fixed in the template in this repo,
  and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) records the errors and
  the fixes. The local structural check passed on the broken
  template; it cannot see either class of error.
- **The analysis step in this version is a deterministic rule-based
  analyzer**, not a managed AI service. Keyword scoring misses
  sarcasm, context, and phrasing outside its word lists. The Bedrock
  or Comprehend integration is designed behind the same interface
  but is not wired to a live service.
- The raw transcript sits in the table briefly between ingest and
  analysis. With real calls, redaction would move into the ingest
  step so PII never reaches storage at all. ARCHITECTURE.md says
  more about that.

## Layout

```
src/            ingest.py, analyze.py, query.py, store.py
sample-data/    seven fictional call transcripts
tests/          unittest suite for the analyzer and query layer
template.yaml   AWS SAM template for the full stack
run_demo.py     the local end to end run
validate_template.py   structural check of the SAM template
ARCHITECTURE.md flow, components, and decisions in full
docs/           DEPLOYMENT.md, the step-by-step live deployment record
```

## License

MIT. See `LICENSE`.
