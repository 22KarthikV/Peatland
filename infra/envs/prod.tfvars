# Real AWS prod. validate/plan only (SPEC D1); never applied in this project.
environment            = "prod"
use_localstack         = false
aws_region             = "eu-west-2"
name_suffix            = "-prod"
enable_iam             = true
noncurrent_expiry_days = 90
