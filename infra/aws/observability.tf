# Log groups, tracing and the cost guardrail. Every log group has an explicit
# bounded retention: a report must cite evidence that is still retrievable, and
# unlimited retention is an unbounded cost (TECHNICAL_SPEC §14).

resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${var.name_prefix}-${var.environment}/cluster"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn

  tags = { Name = "${var.name_prefix}-cluster" }
}

resource "aws_cloudwatch_log_group" "workloads" {
  name              = "/sentinelops/${var.environment}/workloads"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn

  tags = { Name = "${var.name_prefix}-workloads" }
}

resource "aws_cloudwatch_log_group" "vpc_flow" {
  name              = "/sentinelops/${var.environment}/vpc-flow"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn

  tags = { Name = "${var.name_prefix}-vpc-flow" }
}

# X-Ray sampling is explicit rather than default: trace volume is a cost driver
# and the sampled fraction is part of how source quality is interpreted.
resource "aws_xray_sampling_rule" "workloads" {
  rule_name      = "${var.name_prefix}-${var.environment}"
  priority       = 1000
  version        = 1
  reservoir_size = 1
  fixed_rate     = var.xray_fixed_sampling_rate
  host           = "*"
  http_method    = "*"
  url_path       = "*"
  service_name   = "*"
  service_type   = "*"
  resource_arn   = "*"
}

# Cost guardrail. This notifies; it does not cap spend, and it is not a
# substitute for owner budget confirmation before apply.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$SentinelOps"]
  }

  dynamic "notification" {
    for_each = length(var.budget_notification_emails) > 0 ? [50, 80, 100] : []

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.budget_notification_emails
    }
  }
}
