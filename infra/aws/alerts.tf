# Demo email alerts (DEMO_UPGRADE_SPEC WP7): early warnings, proposals waiting for approval and
# outcomes are published by the notifier sidecar of the remediation-controller pod. Recipients are
# operator-provided (sensitive variable, never committed) and must confirm the SNS subscription email.

resource "aws_sns_topic" "demo_alerts" {
  name              = "${var.name_prefix}-${var.environment}-demo-alerts"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "demo_alerts" {
  for_each  = nonsensitive(toset([for index, _ in var.demo_alert_emails : tostring(index)]))
  topic_arn = aws_sns_topic.demo_alerts.arn
  protocol  = "email"
  endpoint  = var.demo_alert_emails[tonumber(each.key)]
}

data "aws_iam_policy_document" "alerts_publisher_pod_identity" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_eks_cluster.main.arn]
    }
  }
}

resource "aws_iam_role" "alerts_publisher" {
  name               = "${var.name_prefix}-alerts-publisher"
  assume_role_policy = data.aws_iam_policy_document.alerts_publisher_pod_identity.json

  tags = { Workload = "demo-alert-notifier" }
}

data "aws_iam_policy_document" "alerts_publisher" {
  statement {
    sid       = "PublishDemoAlertsOnly"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.demo_alerts.arn]
  }
}

resource "aws_iam_role_policy" "alerts_publisher" {
  name   = "demo-alerts-publish"
  role   = aws_iam_role.alerts_publisher.id
  policy = data.aws_iam_policy_document.alerts_publisher.json
}

# The notifier runs in the remediation-controller pod, whose ServiceAccount is payment-rollback.
resource "aws_eks_pod_identity_association" "alerts_publisher" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "sentinelops-b-actions"
  service_account = "payment-rollback"
  role_arn        = aws_iam_role.alerts_publisher.arn
  depends_on      = [aws_eks_addon.pod_identity]
}
