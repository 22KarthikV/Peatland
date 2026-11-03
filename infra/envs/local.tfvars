# LocalStack via tflocal. "-tf" suffix coexists with infra/localstack/init.sh resources.
# IAM is off because the compose LocalStack runs SERVICES=s3,sqs,secretsmanager.
# Provide the dev key via the environment: TF_VAR_api_key=dev-only-key
environment         = "local"
use_localstack      = true
localstack_endpoint = "http://localhost:4566"
name_suffix         = "-tf"
enable_iam          = false
