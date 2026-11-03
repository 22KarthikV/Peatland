# Offline checks for the AWS-only resources (I2). A real `terraform plan` against AWS needs
# credentials and network, so these use a mocked aws provider: nothing leaves the machine.
# Run: terraform -chdir=infra test

mock_provider "aws" {}

variables {
  environment        = "dev"
  use_localstack     = false
  enable_iam         = true
  enable_aws_only    = true
  name_suffix        = "-dev"
  vpc_id             = "vpc-0123456789abcdef0"
  public_subnet_ids  = ["subnet-pub1", "subnet-pub2"]
  private_subnet_ids = ["subnet-prv1", "subnet-prv2"]
  budget_email       = "ops@example.com"
  alarm_email        = "ops@example.com"
}

# Computed values that end up inside the JSON-encoded container definitions are unknown at plan
# time; give them values so the assertions can read the definitions.
override_resource {
  target          = aws_ecr_repository.this["api"]
  override_during = plan
  values = {
    repository_url = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/peatwatch/api"
  }
}
override_resource {
  target          = aws_ecr_repository.this["worker"]
  override_during = plan
  values = {
    repository_url = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/peatwatch/worker"
  }
}
override_resource {
  target          = aws_db_instance.this[0]
  override_during = plan
  values = {
    address = "peatwatch-dev.example.eu-west-2.rds.amazonaws.com"
    master_user_secret = [{
      kms_key_id    = "alias/aws/secretsmanager"
      secret_arn    = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:rds!db-mock-AbCdEf"
      secret_status = "active"
    }]
  }
}

override_resource {
  target          = aws_cloudfront_distribution.web[0]
  override_during = plan
  values = {
    domain_name = "d111111abcdef8.cloudfront.net"
  }
}

run "local_mode_creates_no_aws_only_resources" {
  command = plan
  variables {
    enable_aws_only = false
    use_localstack  = true
    enable_iam      = false
  }
  assert {
    condition     = length(aws_ecs_cluster.this) == 0 && length(aws_db_instance.this) == 0 && length(aws_cloudfront_distribution.web) == 0 && length(aws_budgets_budget.monthly) == 0 && length(aws_ecr_repository.this) == 0
    error_message = "AWS-only resources must not exist when enable_aws_only is false"
  }
}

run "rds_private_encrypted_managed_password" {
  command = plan
  assert {
    condition     = aws_db_instance.this[0].publicly_accessible == false && aws_db_instance.this[0].storage_encrypted == true
    error_message = "RDS must be private and encrypted"
  }
  assert {
    condition     = aws_db_instance.this[0].manage_master_user_password == true && aws_db_instance.this[0].engine == "postgres"
    error_message = "RDS must use a managed master password"
  }
}

run "web_bucket_is_private" {
  command = plan
  assert {
    condition     = aws_s3_bucket_public_access_block.web[0].block_public_acls && aws_s3_bucket_public_access_block.web[0].block_public_policy && aws_s3_bucket_public_access_block.web[0].ignore_public_acls && aws_s3_bucket_public_access_block.web[0].restrict_public_buckets
    error_message = "web bucket must block all public access"
  }
  assert {
    condition     = aws_cloudfront_origin_access_control.web[0].signing_behavior == "always" && aws_cloudfront_distribution.web[0].default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "CloudFront must use OAC and redirect to HTTPS"
  }
}

run "services_private_non_root" {
  command = plan
  assert {
    condition     = alltrue([for s in [aws_ecs_service.api[0], aws_ecs_service.worker[0]] : s.network_configuration[0].assign_public_ip == false && s.launch_type == "FARGATE"])
    error_message = "Fargate tasks must not get public IPs"
  }
  assert {
    condition     = alltrue([for k, t in aws_ecs_task_definition.service : jsondecode(t.container_definitions)[0].user == "app" && jsondecode(t.container_definitions)[0].privileged == false && jsondecode(t.container_definitions)[0].logConfiguration.logDriver == "awslogs"])
    error_message = "containers must run as non-root, unprivileged, with awslogs"
  }
  assert {
    condition     = alltrue([for k, t in aws_ecs_task_definition.service : length(jsondecode(t.container_definitions)[0].secrets) == 2])
    error_message = "DB credentials must come from Secrets Manager"
  }
}

run "alb_http_only_by_default" {
  command = plan
  assert {
    condition     = aws_lb_listener.http[0].port == 80 && aws_lb_listener.http[0].default_action[0].type == "forward" && length(aws_lb_listener.https) == 0
    error_message = "without a certificate the ALB has an HTTP forward listener only"
  }
  assert {
    condition     = aws_lb_target_group.api[0].health_check[0].path == "/health"
    error_message = "health check must be /health"
  }
}

run "alb_https_when_certificate_given" {
  command = plan
  variables {
    alb_certificate_arn = "arn:aws:acm:eu-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"
  }
  assert {
    condition     = aws_lb_listener.https[0].port == 443 && aws_lb_listener.http[0].default_action[0].type == "redirect"
    error_message = "HTTPS listener plus HTTP redirect expected"
  }
}

run "budget_and_alarms_exist" {
  command = plan
  assert {
    condition     = aws_budgets_budget.monthly[0].time_unit == "MONTHLY" && length(aws_budgets_budget.monthly[0].notification) == 4
    error_message = "monthly budget with 3 actual + 1 forecast notifications expected"
  }
  assert {
    condition     = aws_cloudwatch_log_metric_filter.run_failed[0].pattern == "\"run failed\"" && aws_cloudwatch_metric_alarm.dlq_depth[0].metric_name == "ApproximateNumberOfMessagesVisible"
    error_message = "failed-run filter and DLQ alarm expected"
  }
}

run "requires_network_inputs" {
  command = plan
  variables {
    vpc_id = null
  }
  expect_failures = [aws_ecs_cluster.this]
}

run "requires_budget_email" {
  command = plan
  variables {
    budget_email = ""
  }
  expect_failures = [aws_budgets_budget.monthly]
}
