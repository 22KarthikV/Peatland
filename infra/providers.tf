# Providers, backend and the guard that keeps local runs away from real AWS (I1).
#
# Two modes, chosen by var.use_localstack:
#   true  (default): every endpoint points at var.localstack_endpoint, credentials are the
#                    fixed dummy "test"/"test", and the account/credential checks that would
#                    call real AWS are skipped. variables.tf rejects a non-loopback endpoint,
#                    so a typo cannot silently send traffic to AWS (see tests/guard.tftest.hcl).
#   false          : normal AWS provider using the default credential chain (CI uses OIDC, I3).
#
# Local use:   tflocal -chdir=infra apply -var-file=envs/local.tfvars
# Real AWS:    terraform -chdir=infra plan  -var-file=envs/dev.tfvars   (never applied in this project, SPEC D1)

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State backend. Default is local (terraform.tfstate, git-ignored) so `tflocal` works with no
  # setup. To use remote state (real AWS, not applied in this project) replace the block below
  # with a partial S3 backend and fill it per environment at init time:
  #
  #   backend "s3" {}
  #
  #   terraform init -reconfigure \
  #     -backend-config="bucket=peatwatch-tfstate-<account-id>" \
  #     -backend-config="key=peatwatch/<env>/terraform.tfstate" \
  #     -backend-config="region=eu-west-2" \
  #     -backend-config="use_lockfile=true" \
  #     -backend-config="encrypt=true"
  #
  # The state bucket is created once by hand (versioned, encrypted, public access blocked) and is
  # deliberately not managed by this configuration. Backend blocks cannot use variables.
  backend "local" {}
}

provider "aws" {
  region = var.aws_region

  # Dummy credentials in local mode only. Real mode uses the default chain.
  access_key = var.use_localstack ? "test" : null
  secret_key = var.use_localstack ? "test" : null

  skip_credentials_validation = var.use_localstack
  skip_metadata_api_check     = var.use_localstack
  skip_requesting_account_id  = var.use_localstack
  skip_region_validation      = var.use_localstack
  s3_use_path_style           = var.use_localstack

  dynamic "endpoints" {
    for_each = var.use_localstack ? [var.localstack_endpoint] : []
    content {
      s3             = endpoints.value
      sqs            = endpoints.value
      secretsmanager = endpoints.value
      iam            = endpoints.value
      sts            = endpoints.value
    }
  }

  default_tags {
    tags = {
      Project     = "peatwatch"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
