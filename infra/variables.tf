variable "environment" {
  description = "Environment name; used in tags."
  type        = string
  default     = "local"
  validation {
    condition     = contains(["local", "dev", "prod"], var.environment)
    error_message = "environment must be local, dev or prod."
  }
}

variable "aws_region" {
  description = "AWS region (LocalStack accepts any)."
  type        = string
  default     = "us-east-1"
}

variable "use_localstack" {
  description = "Point every endpoint at LocalStack with dummy credentials. Default true so a bare apply can never reach real AWS; dev/prod tfvars set it false."
  type        = bool
  default     = true
}

variable "localstack_endpoint" {
  description = "LocalStack edge URL. Loopback or the compose service name only, so real AWS is never contacted."
  type        = string
  default     = "http://localhost:4566"
  validation {
    condition     = can(regex("^https?://(localhost|127\\.0\\.0\\.1|\\[::1\\]|localstack)(:[0-9]+)?/?$", var.localstack_endpoint))
    error_message = "localstack_endpoint must be http(s)://localhost, 127.0.0.1, [::1] or localstack (port optional)."
  }
}

variable "name_suffix" {
  description = <<-EOT
    Appended to every resource name. The compose init script (infra/localstack/init.sh) owns the
    un-suffixed names (peatwatch-data, peatwatch-jobs, peatwatch/api-key), so the local tfvars use
    "-tf" to coexist with it. Real AWS bucket names are global, so dev/prod use "-dev"/"-prod".
  EOT
  type        = string
  default     = "-tf"
  validation {
    condition     = can(regex("^(-[a-z0-9]+)*$", var.name_suffix))
    error_message = "name_suffix must be empty or lowercase alphanumerics prefixed by dashes, e.g. -tf."
  }
}

variable "enable_iam" {
  description = "Create the api/worker IAM roles and policies. The compose LocalStack runs SERVICES=s3,sqs,secretsmanager (no IAM), so envs/local.tfvars sets this to false."
  type        = bool
  default     = true
}

variable "api_key" {
  description = "Initial value of the peatwatch/api-key secret. Never commit it: pass TF_VAR_api_key. Null creates the secret with no value (set it out of band)."
  type        = string
  default     = null
  sensitive   = true
}

variable "tmp_expiry_days" {
  description = "Days before objects under tmp/ expire."
  type        = number
  default     = 7
}

variable "noncurrent_expiry_days" {
  description = "Days before non-current object versions expire."
  type        = number
  default     = 30
}

variable "task_principal" {
  description = "Service allowed to assume the api and worker roles."
  type        = string
  default     = "ecs-tasks.amazonaws.com"
}
