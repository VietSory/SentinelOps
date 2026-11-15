# Configuration evaluation only: both providers are mocked, every run is plan.
# No AWS/TLS endpoint, credentials, state backend or deployed resource is used.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-central-1a", "eu-central-1b", "eu-central-1c"] }
  }
  # These unrelated provider-rendered policies are not the subject of this
  # test. Keep their stand-in valid JSON; key/caller JSON above is NOT mocked.
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/software-fixture" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-central-1:123456789012:log-group:/software-fixture" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:eu-central-1:123456789012:key/11111111-2222-3333-4444-555555555555" }
  }
  mock_resource "aws_eks_cluster" {
    defaults = { identity = [{ oidc = [{ issuer = "https://oidc.eks.eu-central-1.amazonaws.com/id/SOFTWAREFIXTURE" }] }] }
  }
  mock_resource "aws_db_instance" {
    defaults = { master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:eu-central-1:123456789012:secret:software-fixture" }] }
  }
}
mock_provider "tls" {
  override_during = plan
  mock_data "tls_certificate" {
    defaults = { certificates = [{ sha1_fingerprint = "1111111111111111111111111111111111111111" }] }
  }
}

variables {
  region              = "eu-central-1"
  allowed_account_ids = ["123456789012"]
  environment         = "dev"
}

run "encrypted_log_groups" {
  command = plan

  assert {
    condition = jsondecode(aws_kms_key.main.policy) == {
      Version = "2012-10-17"
      Statement = [
        {
          Sid       = "EnableAccountKeyAdministration"
          Effect    = "Allow"
          Principal = { AWS = "arn:aws:iam::123456789012:root" }
          Action    = ["kms:*"]
          Resource  = "*"
        },
        {
          Sid       = "EncryptDeclaredCloudWatchLogs"
          Effect    = "Allow"
          Principal = { Service = "logs.eu-central-1.amazonaws.com" }
          Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncryptFrom", "kms:ReEncryptTo", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey"]
          Resource  = "*"
          Condition = {
            ArnEquals = {
              "kms:EncryptionContext:aws:logs:arn" = [
                "arn:aws:logs:eu-central-1:123456789012:log-group:/aws/eks/sentinelops-dev/cluster",
                "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/vpc-flow",
                "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/workloads",
              ]
            }
          }
        },
      ]
    }
    error_message = "The key must retain account administration and allow only regional Logs crypto for the three exact log-group ARNs."
  }

  assert {
    condition = toset(jsondecode(aws_kms_key.main.policy).Statement[1].Condition.ArnEquals["kms:EncryptionContext:aws:logs:arn"]) == toset([
      for group in [aws_cloudwatch_log_group.cluster, aws_cloudwatch_log_group.vpc_flow, aws_cloudwatch_log_group.workloads] :
      "arn:aws:logs:eu-central-1:123456789012:log-group:${group.name}"
    ])
    error_message = "Log-group names and the encryption contexts must agree; no ARN stream suffix or unrelated group is allowed."
  }

  assert {
    condition = (
      aws_iam_role_policy.telemetry_logs_key.role == aws_iam_role.telemetry_reader.id &&
      jsondecode(aws_iam_role_policy.telemetry_logs_key.policy) == {
        Version = "2012-10-17"
        Statement = [{
          Effect   = "Allow"
          Action   = ["kms:Decrypt", "kms:DescribeKey"]
          Resource = aws_kms_key.main.arn
          Condition = {
            StringEquals = { "kms:ViaService" = "logs.eu-central-1.amazonaws.com" }
            ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = [
              "arn:aws:logs:eu-central-1:123456789012:log-group:/aws/eks/sentinelops-dev/cluster",
              "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/vpc-flow",
              "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/workloads",
            ] }
          }
        }]
      }
    )
    error_message = "The reader must only decrypt/describe this key via regional Logs for exactly the declared groups."
  }

  assert {
    condition = (
      aws_iam_role_policy.vpc_flow_logs_key.role == aws_iam_role.vpc_flow_log.id &&
      jsondecode(aws_iam_role_policy.vpc_flow_logs_key.policy) == {
        Version = "2012-10-17"
        Statement = [{
          Effect   = "Allow"
          Action   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncryptFrom", "kms:ReEncryptTo", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey"]
          Resource = aws_kms_key.main.arn
          Condition = {
            StringEquals = { "kms:ViaService" = "logs.eu-central-1.amazonaws.com" }
            ArnEquals    = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/vpc-flow" }
          }
        }]
      }
    )
    error_message = "Flow-log delivery must only use this key via regional Logs for its own exact log-group ARN."
  }
}

run "changed_environment_and_region" {
  command = plan
  variables {
    region      = "ap-southeast-1"
    environment = "prod"
    name_prefix = "sentinelops-next"
  }

  assert {
    condition = (
      jsondecode(aws_kms_key.main.policy).Statement[1].Principal.Service == "logs.ap-southeast-1.amazonaws.com" &&
      toset(jsondecode(aws_kms_key.main.policy).Statement[1].Condition.ArnEquals["kms:EncryptionContext:aws:logs:arn"]) == toset([
        "arn:aws:logs:ap-southeast-1:123456789012:log-group:/aws/eks/sentinelops-next-prod/cluster",
        "arn:aws:logs:ap-southeast-1:123456789012:log-group:/sentinelops/prod/vpc-flow",
        "arn:aws:logs:ap-southeast-1:123456789012:log-group:/sentinelops/prod/workloads",
      ])
    )
    error_message = "Region, environment and prefix changes must update the Logs service principal and exact encryption contexts."
  }
}
