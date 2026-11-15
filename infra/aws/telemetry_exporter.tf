# Lane B: outbound telemetry only. This role is not telemetry-reader and cannot
# change log-group retention/encryption, read application state or dispatch actions.
resource "aws_iam_role" "telemetry_exporter" {
  name = "${var.name_prefix}-telemetry-exporter"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.cluster.arn }
      Condition = {
        StringEquals = {
          "${local.oidc_provider_url}:sub" = "system:serviceaccount:${local.namespace}:telemetry-exporter"
          "${local.oidc_provider_url}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
  tags = { Workload = "telemetry-exporter" }
}

resource "aws_iam_role_policy" "telemetry_exporter" {
  name = "telemetry-export"
  role = aws_iam_role.telemetry_exporter.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteCollectorStreams"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "${local.log_group_arns.workloads}:log-stream:otel-metrics-*",
          "${local.log_group_arns.workloads}:log-stream:otel-logs-*",
        ]
      },
      {
        Sid      = "DescribeWorkloadStreams"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogStreams"]
        Resource = "${local.log_group_arns.workloads}:*"
      },
      {
        Sid       = "SendRegionalTraces"
        Effect    = "Allow"
        Action    = ["xray:PutTraceSegments"]
        Resource  = "*" # This API has no resource-level permission.
        Condition = { StringEquals = { "aws:RequestedRegion" = var.region } }
      },
      {
        Sid      = "UseWorkloadLogKeyThroughLogs"
        Effect   = "Allow"
        Action   = local.logs_crypto_actions
        Resource = aws_kms_key.main.arn
        Condition = {
          StringEquals = { "kms:ViaService" = local.logs_service }
          ArnEquals    = { "kms:EncryptionContext:aws:logs:arn" = local.log_group_arns.workloads }
        }
      },
    ]
  })
}
