#!/bin/bash
# Runs inside LocalStack when it is ready. Idempotent.
set -euo pipefail

awslocal s3api head-bucket --bucket peatwatch-data 2>/dev/null || awslocal s3 mb s3://peatwatch-data
awslocal s3api head-bucket --bucket peatwatch-artifacts 2>/dev/null || awslocal s3 mb s3://peatwatch-artifacts
awslocal sqs create-queue --queue-name peatwatch-jobs >/dev/null
awslocal sqs create-queue --queue-name peatwatch-jobs-dlq >/dev/null
awslocal secretsmanager describe-secret --secret-id peatwatch/api-key >/dev/null 2>&1 \
  || awslocal secretsmanager create-secret --name peatwatch/api-key --secret-string dev-only-key >/dev/null
echo "peatwatch init done"
