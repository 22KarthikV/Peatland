# Real AWS dev. validate/plan only (SPEC D1); never applied in this project.
environment    = "dev"
use_localstack = false
aws_region     = "eu-west-2"
name_suffix    = "-dev"
enable_iam     = true

# AWS-only resources. Fill in before a real plan (preconditions fail with a clear message if not):
enable_aws_only = true
# vpc_id             = "vpc-..."
# public_subnet_ids  = ["subnet-...", "subnet-..."]
# private_subnet_ids = ["subnet-...", "subnet-..."]
# budget_email       = "you@example.com"
# alarm_email        = "you@example.com"
# alb_certificate_arn = "arn:aws:acm:..."   # HTTPS; without it the ALB serves HTTP only
