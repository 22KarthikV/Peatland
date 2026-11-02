#!/usr/bin/env bash
# Checks the local stack: PostGIS answers, and LocalStack has the buckets, queue and secret.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T postgis psql -U peatwatch -d peatwatch -tAc "CREATE EXTENSION IF NOT EXISTS postgis; SELECT postgis_version();"
docker compose exec -T localstack awslocal s3api head-bucket --bucket peatwatch-data
docker compose exec -T localstack awslocal s3api head-bucket --bucket peatwatch-artifacts
docker compose exec -T localstack awslocal sqs get-queue-url --queue-name peatwatch-jobs
docker compose exec -T localstack awslocal secretsmanager describe-secret --secret-id peatwatch/api-key >/dev/null
echo "smoke_stack: OK"
