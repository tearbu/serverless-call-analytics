# Live deployment record

This is the step-by-step account of deploying this project to a real
AWS account on 23 November 2026. It records what actually happened,
including two failed attempts and the template defects behind them.
Both defects are fixed in the `template.yaml` now in this repo, so
the template here is the one that deployed, not the one that was
first published.

A note on evidence: this document has no screenshots. Each console
stage (the stack, the bucket, the functions, the table items) was
visually verified in the AWS console during the run. The outputs
reproduced below are the real command results from that run.

## Deployment facts

- Date: 23 November 2026. Stack creation started 09:10:15 EST.
- Region: us-east-1.
- Account: 762103021017.
- Stack: serverless-call-analytics, reached CREATE_COMPLETE, ran
  for the verification below, and was deleted the same day (see
  Teardown at the end of this document).
- Deploy path: clone this repo in AWS CloudShell, then
  `aws cloudformation package` and `aws cloudformation deploy`.

## Prerequisites

- An AWS account with permission to create S3 buckets, a DynamoDB
  table, Lambda functions, API Gateway APIs, and IAM roles.
- AWS CloudShell, which provides the AWS CLI and git in the browser.
  No local installs and no SAM CLI were used.
- The seven fictional transcripts in `sample-data/` as the test load.

## Step 1: Sign in and set the region

Signed in to the AWS console as the account owner and set the
console region to US East (N. Virginia), us-east-1. Every command
below ran in that region. CloudShell inherits the console region, so
the CLI calls needed no region flags.

## Step 2: Clone the project

In CloudShell:

```bash
git clone https://github.com/tearbu/serverless-call-analytics.git
cd serverless-call-analytics
```

The clone is the published repo, so the template reviewed and
deployed in the next steps started out identical to what was on
GitHub at the time.

## Step 3: Review the template

Read through `template.yaml` before deploying. It defines five
resources: the transcripts bucket, the calls table with a NEW_IMAGE
stream and on-demand billing, and three functions (ingest, analyze,
query), each with its own scoped IAM policy. The events wire the
pipeline together: S3 object creation triggers ingest, the table
stream triggers analyze, and three API routes reach the query
function.

The local structural check had already passed on this template.
That check parses the YAML and asserts the resource shape. It cannot
see SAM transform rules or CloudFormation dependency cycles, and
both of those were about to matter.

## Step 4: Create the artifacts bucket

`aws cloudformation package` needs an S3 bucket to hold the packaged
function code:

```bash
aws s3 mb s3://serverless-call-analytics-artifacts-762103021017
```

## Step 5: Package

```bash
aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket serverless-call-analytics-artifacts-762103021017 \
  --output-template-file packaged.yaml
```

Packaging succeeded. The failures came at deploy time, when the SAM
transform and CloudFormation checked the template itself.

## Step 6: First deploy attempt, and the DynamoDB event defect

The first `aws cloudformation deploy` failed in the SAM transform:

```
Resource with id [AnalyzeFunctionNewCallRecord] is invalid.
property Table not defined for resource of type DynamoDB
```

The AnalyzeFunction stream event in the published template carried
both of these lines:

```yaml
Table: !Ref CallsTable
Stream: !GetAtt CallsTable.StreamArn
```

A SAM DynamoDB event takes a Stream property. It has no Table
property. The fix was to delete the `Table: !Ref CallsTable` line
and leave the stream reference to identify the table. That deletion
is in the repo template now.

## Step 7: Second deploy attempt, and the circular dependency

With the event fixed, the deploy failed again:

```
Circular dependency between resources:
[IngestFunctionNewTranscriptPermission, CallsBucket,
 IngestFunction, IngestFunctionRole]
```

The cause was the IngestFunction S3 policy, which addressed the
bucket like this:

```yaml
Resource: !Sub "${CallsBucket.Arn}/*"
```

The function role depended on the bucket ARN, while the bucket's
event notification and the Lambda permission depended back on the
function. CloudFormation could not order the creates. The fix
addresses the same bucket by name instead of by reference:

```yaml
Resource: !Sub "arn:${AWS::Partition}:s3:::${AWS::StackName}-transcripts/*"
```

The bucket is named `${AWS::StackName}-transcripts` in the same
template, so the policy still covers exactly the transcripts bucket
and nothing else, but the role no longer depends on the bucket
resource. That change is in the repo template now.

## Step 8: Successful deploy

```bash
aws cloudformation deploy \
  --template-file packaged.yaml \
  --stack-name serverless-call-analytics \
  --capabilities CAPABILITY_IAM
```

The stack reached CREATE_COMPLETE. The `--capabilities` flag
acknowledges that the template creates IAM roles, one per function.

Stack outputs:

| Output | Value |
| --- | --- |
| BucketName | serverless-call-analytics-transcripts |
| TableName | serverless-call-analytics-calls |
| ApiUrl | https://pb6eii1y8k.execute-api.us-east-1.amazonaws.com/Prod |

## Step 9: Upload the transcripts

Uploaded all seven sample transcripts to the transcripts bucket
from CloudShell:

```bash
aws s3 cp sample-data/ s3://serverless-call-analytics-transcripts/ --recursive
```

No function was invoked by hand at any point. Each upload fired the
S3 event, ingest stored the raw record, the new item fired the
table stream, and analyze classified the record and wrote the
analysis back. The wiring in the template did the whole run.

## Step 10: Verify the table

A DynamoDB scan of serverless-call-analytics-calls showed 7 items,
every one with status analyzed. The counts:

- Category: billing 2, technical 2, access 1, outage 1, other 1.
- Sentiment: negative 3, neutral 2, positive 2.
- Urgency: high 1, medium 2, low 4.
- PII flagged: 1 call, transcript 006, with card, ssn, phone, and
  email values found and redacted in the stored record.

These match the local simulation's results on the same seven
transcripts exactly, which is the point of the store layer seam:
same handlers, same outcome, two environments.

## Step 11: Test the API

Called the stats endpoint on the live API:

```
GET https://pb6eii1y8k.execute-api.us-east-1.amazonaws.com/Prod/calls/stats
```

Response body:

```json
{"total_calls":7,"by_category":{"access":1,"billing":2,"other":1,"technical":2,"outage":1},"by_sentiment":{"neutral":2,"negative":3,"positive":2},"by_urgency":{"low":4,"medium":2,"high":1},"pii_flagged_count":1}
```

The numbers match the table scan exactly. `GET /calls` on the same
base URL also returned the analyzed records.

## Teardown

The stack ran for the verification above and was then deleted on
the same day, 23 November 2026, at the owner's direction, so that
nothing from this project keeps billing.

The deletion steps, in order:

1. Emptied the transcripts bucket (the seven sample files). The
   bucket itself was a stack resource and was removed with the
   stack.
2. Deleted the stack:

   ```bash
   aws cloudformation delete-stack --stack-name serverless-call-analytics
   ```

   After the delete completed, `aws cloudformation describe-stacks`
   returned "Stack with id serverless-call-analytics does not
   exist", which is the confirmation that the stack, the table, the
   three functions, and the API are gone.
3. Emptied and deleted the artifacts bucket from step 4,
   serverless-call-analytics-artifacts-762103021017. That bucket was
   created by hand and was never part of the stack, so it had to be
   removed separately.

Final checks in us-east-1 show no Lambda functions, no DynamoDB
tables, and no REST APIs left from this project. The API URL
recorded in step 8 no longer responds. Everything in this document
above the teardown is the record of the run as it happened; to run
it again, repeat the steps from the top.

## What this run changed in the repo

- `template.yaml`: the two fixes from steps 6 and 7. The template in
  this repo is now identical in effect to the one that deployed.
- This document.
- `README.md`: the Output section now carries the live results, and
  the limits section no longer says the project was never deployed.
