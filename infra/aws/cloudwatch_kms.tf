# Lane B: encryption bindings for the declared CloudWatch log groups.
# Derive names without referring to log-group resources: those resources depend
# on this key, and referencing their ARNs from its policy would create a cycle.
locals {
  log_group_names = {
    cluster   = "/aws/eks/${var.name_prefix}-${var.environment}/cluster"
    workloads = "/${var.name_prefix}/${var.environment}/workloads"
    vpc_flow  = "/${var.name_prefix}/${var.environment}/vpc-flow"
  }
  log_group_arns = {
    for name, path in local.log_group_names : name =>
    "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${path}"
  }
  logs_service = "logs.${var.region}.amazonaws.com"
  logs_crypto_actions = [
    "kms:Encrypt", "kms:Decrypt", "kms:ReEncryptFrom", "kms:ReEncryptTo",
    "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey",
  ]
  cloudwatch_key_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        # Preserve the AWS default account-administration/delegation statement.
        # This is a key policy, not a kms:* permission attached to a workload.
        Sid       = "EnableAccountKeyAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = ["kms:*"]
        Resource  = "*"
      },
      {
        Sid       = "EncryptDeclaredCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = local.logs_service }
        Action    = local.logs_crypto_actions
        Resource  = "*" # In a key policy, this means only the key carrying it.
        Condition = {
          ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = sort(values(local.log_group_arns)) }
        }
      },
    ]
  }
}

# Callers of encrypted Logs also need KMS access through the regional service.
# Neither role can use this permission for direct KMS calls or other log groups.
resource "aws_iam_role_policy" "telemetry_logs_key" {
  name = "telemetry-logs-key-read"
  role = aws_iam_role.telemetry_reader.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:Decrypt", "kms:DescribeKey"]
      Resource = aws_kms_key.main.arn
      Condition = {
        StringEquals = { "kms:ViaService" = local.logs_service }
        ArnEquals    = { "kms:EncryptionContext:aws:logs:arn" = sort(values(local.log_group_arns)) }
      }
    }]
  })
}

resource "aws_iam_role_policy" "vpc_flow_logs_key" {
  name = "flow-log-key-delivery"
  role = aws_iam_role.vpc_flow_log.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = local.logs_crypto_actions
      Resource = aws_kms_key.main.arn
      Condition = {
        StringEquals = { "kms:ViaService" = local.logs_service }
        ArnEquals    = { "kms:EncryptionContext:aws:logs:arn" = local.log_group_arns.vpc_flow }
      }
    }]
  })
}
