# CloudWatch alarms (I2): failed pipeline runs and dead-lettered jobs. AWS-only, validate/plan
# only; gated on var.enable_aws_only.
#
# FAILED RUNS. A metric filter counts a log line from the worker log group.
#   Filter pattern (exact, quoted term):   "run failed"
# api/src/api/worker.py `Worker._fail` is where every failure is recorded (UPDATE runs SET
# status='failed'), but it currently writes NO log line, so this filter matches nothing until the
# worker logs one. REQUIRED worker change (outside infra/, so not made here), first statement of
# `_fail`:
#   logger.warning("run failed run_id=%s code=%s", run_id, code)
# The default `logging.basicConfig` format prefixes the level and logger name, which does not
# affect a term match.
#
# DLQ. Any visible message in the dead-letter queue means a job exhausted its 3 receives.

variable "alarm_email" {
  description = "Address subscribed to the alarm SNS topic. Empty creates the topic without a subscription."
  type        = string
  default     = ""
}

locals {
  run_failed_pattern = "\"run failed\""
}

resource "aws_sns_topic" "alarms" {
  count = local.aws_only
  name  = "peatwatch-alarms${var.name_suffix}"
  # Not encrypted with alias/aws/sns on purpose: CloudWatch cannot publish to a topic that uses
  # the AWS-managed key. A customer-managed key with a CloudWatch grant would be needed.
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.enable_aws_only && var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_log_metric_filter" "run_failed" {
  count          = local.aws_only
  name           = "peatwatch-run-failed${var.name_suffix}"
  log_group_name = aws_cloudwatch_log_group.service["worker"].name
  pattern        = local.run_failed_pattern

  metric_transformation {
    name      = "RunsFailed"
    namespace = "PeatWatch${var.name_suffix}"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "run_failed" {
  count               = local.aws_only
  alarm_name          = "peatwatch-run-failed${var.name_suffix}"
  alarm_description   = "At least one pipeline run failed in 5 minutes (worker log line: run failed)."
  namespace           = "PeatWatch${var.name_suffix}"
  metric_name         = "RunsFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms[0].arn]

  depends_on = [aws_cloudwatch_log_metric_filter.run_failed]
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  count               = local.aws_only
  alarm_name          = "peatwatch-dlq-not-empty${var.name_suffix}"
  alarm_description   = "Jobs are in the dead-letter queue (redrive per contracts/queue.md)."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms[0].arn]

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }
}
