# Core data-plane resources: buckets, job queue + DLQ, API key secret.
# Names and settings match infra/localstack/init.sh, contracts/cog-layout.md (section 1) and
# contracts/queue.md (Settings), with var.name_suffix appended.

locals {
  buckets = {
    data      = "peatwatch-data${var.name_suffix}"
    artifacts = "peatwatch-artifacts${var.name_suffix}"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  # Versioning is on, so old versions of replaced products (cog-layout.md section 6) are kept
  # for a while and then dropped.
  depends_on = [aws_s3_bucket_versioning.this]

  rule {
    id     = "expire-tmp"
    status = "Enabled"
    filter {
      prefix = "tmp/"
    }
    expiration {
      days = var.tmp_expiry_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_expiry_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }
}

# Dead-letter queue first: the jobs queue references it. Retention 14 days (queue.md).
resource "aws_sqs_queue" "dlq" {
  name                      = "peatwatch-jobs-dlq${var.name_suffix}"
  message_retention_seconds = 14 * 24 * 3600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "jobs" {
  name                       = "peatwatch-jobs${var.name_suffix}"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 4 * 24 * 3600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

# Lets operators redrive DLQ messages back to the jobs queue (queue.md, delivery behaviour 6).
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}

resource "aws_secretsmanager_secret" "api_key" {
  name                    = "peatwatch/api-key${var.name_suffix}"
  description             = "Shared API key checked by the PeatWatch API (X-API-Key)."
  recovery_window_in_days = var.environment == "prod" ? 7 : 0
}

# The value comes from var.api_key (TF_VAR_api_key), never from git. ignore_changes means a
# rotated value set out of band is not overwritten by the next apply.
resource "aws_secretsmanager_secret_version" "api_key" {
  count         = var.api_key == null ? 0 : 1
  secret_id     = aws_secretsmanager_secret.api_key.id
  secret_string = var.api_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}
