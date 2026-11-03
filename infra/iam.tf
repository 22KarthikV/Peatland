# Least-privilege roles for the api and worker tasks. Every statement names specific ARNs; the
# only wildcard is the object-key suffix "/*" that S3 object actions require. Built with
# jsonencode (no data-source calls), so `validate`/`plan` need no AWS access.
# Skipped when var.enable_iam is false (compose LocalStack has no IAM service).

locals {
  data_arn      = aws_s3_bucket.this["data"].arn
  artifacts_arn = aws_s3_bucket.this["artifacts"].arn

  assume_role = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = var.task_principal }
      Action    = "sts:AssumeRole"
    }]
  })

  api_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Boundary upload and presigned download URLs (signed with the task role) on the data
        # bucket. ListBucket below makes a missing key return 404 instead of 403.
        Sid      = "DataObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${local.data_arn}/*"
      },
      {
        # Read run logs and exports; the worker is the writer.
        Sid      = "ArtifactsRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${local.artifacts_arn}/*"
      },
      {
        Sid      = "ListBuckets"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.data_arn, local.artifacts_arn]
      },
      {
        # Producer only: send messages. GetQueueUrl resolves the name; no receive or delete.
        Sid      = "EnqueueJobs"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        # Single secret, read only (API_KEY_SECRET_ID).
        Sid      = "ReadApiKey"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.api_key.arn
      },
    ]
  })

  worker_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Reads inputs and writes products, sidecars, models, logs and exports (cog-layout.md).
        # No DeleteObject: replaced objects are superseded by versioning, not deleted.
        Sid      = "ObjectsReadWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${local.data_arn}/*", "${local.artifacts_arn}/*"]
      },
      {
        Sid      = "ListBuckets"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.data_arn, local.artifacts_arn]
      },
      {
        # Consumer: receive, delete on success, extend visibility while a run is in progress.
        # No send, and no access to the DLQ (SQS moves messages there itself).
        Sid    = "ConsumeJobs"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueUrl",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
    ]
  })
}

resource "aws_iam_role" "api" {
  count              = var.enable_iam ? 1 : 0
  name               = "peatwatch-api${var.name_suffix}"
  assume_role_policy = local.assume_role
}

resource "aws_iam_role_policy" "api" {
  count  = var.enable_iam ? 1 : 0
  name   = "peatwatch-api-access"
  role   = aws_iam_role.api[0].id
  policy = local.api_policy
}

resource "aws_iam_role" "worker" {
  count              = var.enable_iam ? 1 : 0
  name               = "peatwatch-worker${var.name_suffix}"
  assume_role_policy = local.assume_role
}

resource "aws_iam_role_policy" "worker" {
  count  = var.enable_iam ? 1 : 0
  name   = "peatwatch-worker-access"
  role   = aws_iam_role.worker[0].id
  policy = local.worker_policy
}
