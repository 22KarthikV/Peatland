# ADR 0001: LocalStack scope and terms

Status: accepted (2026-11-02)

## Context
D1 in `SPEC.md` says AWS is emulated with LocalStack only. In 2026 LocalStack changed its terms: the image needs an account and `LOCALSTACK_AUTH_TOKEN`, including on the free Hobby plan (non-commercial use). Sources disagree on plan details, so re-check the LocalStack pricing page before relying on them.

## Decision
- Run `localstack/localstack:2026.10.4` (pinned) with `SERVICES=s3,sqs,secretsmanager`.
- The token lives in a git-ignored `.env`; `.env.example` documents it. CI will need the token as a repository secret (F3).
- The image reports edition `pro` with a licence activated by the Hobby token. Treat service coverage beyond S3, SQS and Secrets Manager as unverified until infra (I1-I4) tests it.
- If the terms change, the fallback is `moto` server, with D1 amended.

## Verified in F2
- S3 (2 buckets), SQS (job queue and DLQ) and Secrets Manager (dev secret) are created by `infra/localstack/init.sh` and checked by `scripts/smoke_stack.sh`.
- PostGIS 3.4 (`postgis/postgis:16-3.4`) answers `postgis_version()`.
- The image has no `curl`; the healthcheck uses `python3`.
- `awslocal` prints a deprecation notice (replaced by `lstk aws`); harmless, revisit in I1.
- The image's Python is 3.13, consistent with D8.

## Consequences
Contributors need a free LocalStack account to run the stack. Not yet checked: Linux CI runner behaviour.
