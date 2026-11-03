# I2 plan evidence (AWS-only resources)

Date: 2026-11-03. Terraform v1.17.1, aws provider `~> 6.0` (from `infra/.terraform.lock.hcl`).

## What was run

```
terraform -chdir=infra fmt -check     # clean
terraform -chdir=infra validate       # Success! The configuration is valid.
terraform -chdir=infra test           # Success! 12 passed, 0 failed.
tflocal -chdir=infra plan -var-file=envs/local.tfvars   # Plan: 14 to add (I1 resources only)
```

`terraform test` output (`infra/tests/aws_only.tftest.hcl` plus the I1 `guard.tftest.hcl`):

```
tests\aws_only.tftest.hcl... pass
  run "local_mode_creates_no_aws_only_resources"... pass
  run "rds_private_encrypted_managed_password"... pass
  run "web_bucket_is_private"... pass
  run "services_private_non_root"... pass
  run "alb_http_only_by_default"... pass
  run "alb_https_when_certificate_given"... pass
  run "budget_and_alarms_exist"... pass
  run "requires_network_inputs"... pass
  run "requires_budget_email"... pass
tests\guard.tftest.hcl... pass
  run "default_endpoint_is_loopback"... pass
  run "rejects_real_aws_endpoint"... pass
  run "rejects_lookalike_host"... pass
Success! 12 passed, 0 failed.
```

## What the tests assert

With `enable_aws_only = true` on a mocked aws provider (`mock_provider "aws"`, `command = plan`):

- RDS: `publicly_accessible = false`, `storage_encrypted = true`, `manage_master_user_password = true`, engine `postgres`.
- Web bucket: all four public access block flags true; CloudFront uses OAC (`signing_behavior = always`) and `redirect-to-https`.
- ECS: api and worker are FARGATE with `assign_public_ip = false`; each container definition has `user = "app"`, `privileged = false`, the `awslogs` driver, and two secrets (DB user and password from Secrets Manager).
- ALB: HTTP:80 forward listener and no HTTPS listener by default; with a certificate ARN, an HTTPS:443 listener and an HTTP redirect. Target group health check is `/health`.
- Budget: monthly, 3 actual plus 1 forecast notification. Failed-run metric filter pattern is `"run failed"`; DLQ alarm uses `ApproximateNumberOfMessagesVisible`.
- Guards: missing VPC or subnets fails a precondition; empty `budget_email` fails a precondition.
- With `enable_aws_only = false` none of the AWS-only resources exist. A `tflocal plan` with `envs/local.tfvars` shows only the I1 resources.

## What was NOT verified (honest limits)

- No real `terraform plan` against AWS was run. It needs credentials and API calls (provider schema validation passes offline, but a plan refreshes and resolves data), and this project never contacts real AWS.
- The mock provider returns fake computed values, so tests check configuration properties, not what AWS would accept. Not checked: instance class availability in the chosen region, that engine version `16` resolves, ALB and target group name length limits, ACM certificate validity, service quotas, IAM propagation.
- Nothing was applied anywhere, including LocalStack (the compose LocalStack has no ECS, RDS, ALB, CloudFront or Budgets).
- The container start-up command (builds `DATABASE_URL` from the injected DB secret) and the `/health` container check were reasoned from the Dockerfiles and `api/src/api/main.py`, not run in ECS.
- The "run failed" metric filter matches nothing until `Worker._fail` logs that line (see `infra/alarms.tf`).
