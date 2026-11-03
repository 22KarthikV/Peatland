output "data_bucket" {
  value = aws_s3_bucket.this["data"].bucket
}

output "artifacts_bucket" {
  value = aws_s3_bucket.this["artifacts"].bucket
}

output "jobs_queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "jobs_queue_arn" {
  value = aws_sqs_queue.jobs.arn
}

output "jobs_dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "api_key_secret_name" {
  value = aws_secretsmanager_secret.api_key.name
}

output "api_role_arn" {
  value = one(aws_iam_role.api[*].arn)
}

output "worker_role_arn" {
  value = one(aws_iam_role.worker[*].arn)
}
