# Monthly cost guard (I2). AWS-only, validate/plan only; gated on var.enable_aws_only.
# AWS Budgets alerts, it does not stop spending. Budgets is a global service (us-east-1 API).

variable "budget_email" {
  description = "Address that receives budget alerts. Required when enable_aws_only is true."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly cost limit in USD."
  type        = number
  default     = 60
}

variable "budget_actual_thresholds" {
  description = "Percentages of the limit at which an ACTUAL-spend alert fires."
  type        = list(number)
  default     = [50, 80, 100]
}

variable "budget_forecast_threshold" {
  description = "Percentage of the limit at which a FORECASTED-spend alert fires."
  type        = number
  default     = 100
}

resource "aws_budgets_budget" "monthly" {
  count        = local.aws_only
  name         = "peatwatch-monthly${var.name_suffix}"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.budget_actual_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.budget_forecast_threshold
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }

  lifecycle {
    precondition {
      condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_email))
      error_message = "enable_aws_only requires budget_email to be a valid address."
    }
  }
}
