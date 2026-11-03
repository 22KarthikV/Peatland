# Guard tests: local mode must never be pointed at anything but a loopback/compose endpoint.
# Run: terraform -chdir=infra test   (plan only, no network)

variables {
  use_localstack = true
  enable_iam     = false
}

run "default_endpoint_is_loopback" {
  command = plan
  assert {
    condition     = can(regex("^http://localhost:4566$", var.localstack_endpoint))
    error_message = "default endpoint must be local LocalStack"
  }
}

run "rejects_real_aws_endpoint" {
  command = plan
  variables {
    localstack_endpoint = "https://s3.amazonaws.com"
  }
  expect_failures = [var.localstack_endpoint]
}

run "rejects_lookalike_host" {
  command = plan
  variables {
    localstack_endpoint = "http://localhost.evil.example.com:4566"
  }
  expect_failures = [var.localstack_endpoint]
}
