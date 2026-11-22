# Configuration evaluation only: both providers are mocked, every run is plan.
# No AWS/TLS endpoint, credentials, state backend or deployed resource is used.
mock_provider "aws" {
  override_during = plan
  mock_resource "aws_cognito_user_pool" {
    defaults = {
      id       = "eu-central-1_SOFTWAREFIXTURE"
      endpoint = "cognito-idp.eu-central-1.amazonaws.com/eu-central-1_SOFTWAREFIXTURE"
    }
  }
  mock_resource "aws_cognito_user_pool_client" {
    defaults = { id = "softwarefixtureclient" }
  }
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
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.eu-central-1.amazonaws.com/id/SOFTWAREFIXTURE" }
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

run "operator_identity_is_disabled_by_default" {
  command = plan
  assert {
    condition = (
      length(aws_cognito_user_pool.operators) == 0 &&
      length(aws_cognito_user_pool_client.operator) == 0 &&
      length(aws_cognito_resource_server.operator_api) == 0 &&
      length(aws_cognito_user_pool_domain.operator) == 0 &&
      output.operator_oidc_binding == null &&
      output.operator_login_domain == null
    )
    error_message = "Baseline plans must not create a provider, client, domain or public identity binding implicitly."
  }
}

run "operator_identity_has_read_and_proposal_approval_and_requires_enrollment" {
  command = plan
  variables {
    operator_identity_enabled = true
    operator_callback_urls    = ["https://operator.example.invalid/callback"]
  }
  assert {
    condition = (
      aws_cognito_user_pool.operators[0].deletion_protection == "ACTIVE" &&
      aws_cognito_user_pool.operators[0].mfa_configuration == "ON" &&
      aws_cognito_user_pool.operators[0].software_token_mfa_configuration[0].enabled &&
      aws_cognito_user_pool.operators[0].admin_create_user_config[0].allow_admin_create_user_only &&
      aws_cognito_user_pool.operators[0].account_recovery_setting[0].recovery_mechanism == toset([{ name = "admin_only", priority = 1 }]) &&
      aws_cognito_user_pool.operators[0].password_policy[0].minimum_length == 16
    )
    error_message = "Only explicitly enrolled operators with mandatory TOTP may sign in; preserve deletion protection."
  }
  assert {
    condition = (
      !aws_cognito_user_pool_client.operator[0].generate_secret &&
      aws_cognito_user_pool_client.operator[0].enable_token_revocation &&
      aws_cognito_user_pool_client.operator[0].allowed_oauth_flows == toset(["code"]) &&
      aws_cognito_user_pool_client.operator[0].allowed_oauth_scopes == toset(["openid", "sentinelops/incidents.read", "sentinelops/remediation.approve"]) &&
      aws_cognito_resource_server.operator_api[0].identifier == "sentinelops" &&
      aws_cognito_resource_server.operator_api[0].scope == toset([{
        scope_name        = "incidents.read"
        scope_description = "Read incident, explanation and action observations; no writes or dispatch"
        }, {
        scope_name        = "remediation.approve"
        scope_description = "Approve an already proposed runbook proposal; no arbitrary dispatch; all executor guards still apply"
      }]) &&
      aws_cognito_user_pool_client.operator[0].callback_urls == toset(["https://operator.example.invalid/callback"]) &&
      aws_cognito_user_pool_client.operator[0].access_token_validity == 15 &&
      aws_cognito_user_pool_client.operator[0].token_validity_units[0].access_token == "minutes"
    )
    error_message = "The public operator client must use only openid/read/proposal-approval scopes and code flow, short-lived access tokens and no client secret; executor guards still apply."
  }
  assert {
    condition = output.operator_oidc_binding == {
      issuer         = "https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_SOFTWAREFIXTURE"
      audience       = "softwarefixtureclient"
      jwks_url       = "https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_SOFTWAREFIXTURE/.well-known/jwks.json"
      audience_claim = "client_id"
      operator_scope = "sentinelops/incidents.read"
      approval_scope = "sentinelops/remediation.approve"
    }
    error_message = "Renderer configuration must match Cognito access-token client_id, operator_scope and approval_scope (SENTINELOPS_OIDC_APPROVAL_SCOPE)."
  }
  assert {
    condition     = output.operator_login_domain == "https://sentinelops-dev-123456789012-operators.auth.eu-central-1.amazoncognito.com"
    error_message = "The public login URL must use the declared domain and region, without credentials or tokens."
  }
}

run "operator_identity_requires_explicit_callback" {
  command = plan
  variables {
    operator_identity_enabled = true
  }
  expect_failures = [aws_cognito_user_pool.operators]
}

run "operator_login_domain_follows_demo_region" {
  command = plan
  variables {
    region                    = "ap-southeast-1"
    operator_identity_enabled = true
    operator_callback_urls    = ["https://operator.example.invalid/oauth/callback"]
  }
  assert {
    condition     = output.operator_login_domain == "https://sentinelops-dev-123456789012-operators.auth.ap-southeast-1.amazoncognito.com"
    error_message = "The demo login URL must follow ap-southeast-1 rather than a fixed region."
  }
}

run "operator_callback_rejects_credential_urls" {
  command = plan
  variables {
    operator_callback_urls = ["https://user:password@operator.example.invalid/callback"]
  }
  expect_failures = [var.operator_callback_urls]
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
        "arn:aws:logs:ap-southeast-1:123456789012:log-group:/sentinelops-next/prod/vpc-flow",
        "arn:aws:logs:ap-southeast-1:123456789012:log-group:/sentinelops-next/prod/workloads",
      ])
    )
    error_message = "Region, environment and prefix changes must update the Logs service principal and exact encryption contexts."
  }

  assert {
    condition = (
      aws_cloudwatch_dashboard.operations.dashboard_name == "sentinelops-next-prod-operations" &&
      alltrue([for widget in jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets :
        widget.properties.region == "ap-southeast-1" if widget.type != "text"
      ]) &&
      strcontains(jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[1].properties.metrics[0][0].expression, "SentinelOps/prod") &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[7].properties.metrics[0][3] == "sentinelops-next-control-prod" &&
      startswith(jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[10].properties.query, "SOURCE '/sentinelops-next/prod/workloads'")
    )
    error_message = "Dashboard metrics, database and log source must follow the actual region/environment/prefix, without stale dev bindings."
  }
}

run "operations_dashboard_preserves_measurement_boundaries" {
  command = plan

  assert {
    condition = (
      output.operations_dashboard_name == "sentinelops-dev-operations" &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).start == "-PT1H" &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).periodOverride == "inherit" &&
      length(jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets) == 11
    )
    error_message = "The operator view must have its explicit time range and all business/database/log panels."
  }

  assert {
    condition = jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[3].properties.metrics == [[{
      id         = "e1"
      expression = "SEARCH('{\"SentinelOps/dev\",\"service\",\"route\",\"status_code\"} MetricName=\"sentinelops_http_requests\" service=\"payment-service\" route=\"/v1/payments/charge\"', 'Sum', 60)"
    }]]
    error_message = "Business request series must use exact EMF namespace/dimensions and canonical business routes, excluding probe traffic."
  }

  assert {
    condition = (
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[7].properties.metrics == [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "sentinelops-control-dev"]] &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[8].properties.stat == "Maximum" &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[9].properties.stat == "Minimum" &&
      strcontains(jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[10].properties.query, "filter @logStream like /^otel-logs-/")
    )
    error_message = "Control DB metrics must bind the actual RDS instance and raw OTLP logs must exclude the EMF stream."
  }

  assert {
    condition = alltrue([for index, metric in [
      { name = "CPUUtilization", stat = "Average", unit = "Percent" },
      { name = "DatabaseConnections", stat = "Maximum", unit = "Count" },
      { name = "FreeStorageSpace", stat = "Minimum", unit = "Bytes" },
      ] : (
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[7 + index].properties.metrics == [["AWS/RDS", metric.name, "DBInstanceIdentifier", "sentinelops-control-dev"]] &&
      length(jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[7 + index].properties.metrics[0]) == 4 &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[7 + index].properties.stat == metric.stat &&
      jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[7 + index].properties.title == "Control PostgreSQL ${metric.name} (${metric.unit})"
    )])
    error_message = "Each RDS metric must retain its four-item tuple, native unit in the title and intended statistic; unit is not a metric rendering option."
  }

  assert {
    condition = (
      alltrue([for widget in jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets :
        widget.properties.period == 60 && widget.properties.view == "timeSeries" if widget.type == "metric"
      ]) &&
      !strcontains(aws_cloudwatch_dashboard.operations.dashboard_body, "FILL(") &&
      !strcontains(aws_cloudwatch_dashboard.operations.dashboard_body, "request_duration_ms") &&
      !strcontains(aws_cloudwatch_dashboard.operations.dashboard_body, "p95") &&
      strcontains(jsondecode(aws_cloudwatch_dashboard.operations.dashboard_body).widgets[0].properties.markdown, "unknown")
    )
    error_message = "Missing observations must not be filled as healthy; no histogram percentile or model readiness may be inferred."
  }
}

run "exporter_has_only_scoped_delivery_authority" {
  command = plan

  assert {
    condition = (
      aws_iam_role.telemetry_exporter.name == "sentinelops-telemetry-exporter" &&
      jsondecode(aws_iam_role.telemetry_exporter.assume_role_policy) == {
        Version = "2012-10-17"
        Statement = [{
          Effect    = "Allow"
          Action    = "sts:AssumeRoleWithWebIdentity"
          Principal = { Federated = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.eu-central-1.amazonaws.com/id/SOFTWAREFIXTURE" }
          Condition = {
            StringEquals = {
              "oidc.eks.eu-central-1.amazonaws.com/id/SOFTWAREFIXTURE:sub" = "system:serviceaccount:sentinelops:telemetry-exporter"
              "oidc.eks.eu-central-1.amazonaws.com/id/SOFTWAREFIXTURE:aud" = "sts.amazonaws.com"
            }
          }
        }]
      }
    )
    error_message = "Only the dedicated exporter service account and STS audience may assume the exporter role."
  }

  assert {
    condition = (
      aws_iam_role_policy.telemetry_exporter.role == aws_iam_role.telemetry_exporter.id &&
      jsondecode(aws_iam_role_policy.telemetry_exporter.policy) == {
        Version = "2012-10-17"
        Statement = [
          {
            Sid    = "WriteCollectorStreams"
            Effect = "Allow"
            Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
            Resource = [
              "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/workloads:log-stream:otel-metrics-*",
              "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/workloads:log-stream:otel-logs-*",
            ]
          },
          {
            Sid      = "DescribeWorkloadStreams"
            Effect   = "Allow"
            Action   = ["logs:DescribeLogStreams"]
            Resource = "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/workloads:*"
          },
          {
            Sid       = "SendRegionalTraces"
            Effect    = "Allow"
            Action    = ["xray:PutTraceSegments"]
            Resource  = "*"
            Condition = { StringEquals = { "aws:RequestedRegion" = "eu-central-1" } }
          },
          {
            Sid      = "UseWorkloadLogKeyThroughLogs"
            Effect   = "Allow"
            Action   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncryptFrom", "kms:ReEncryptTo", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey"]
            Resource = aws_kms_key.main.arn
            Condition = {
              StringEquals = { "kms:ViaService" = "logs.eu-central-1.amazonaws.com" }
              ArnEquals    = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:eu-central-1:123456789012:log-group:/sentinelops/dev/workloads" }
            }
          },
        ]
      }
    )
    error_message = "Exporter permissions must not expand beyond named workload streams, regional trace writes and the workload Logs key context."
  }

  assert {
    condition = output.collector_bindings == {
      region                    = "eu-central-1"
      environment               = "dev"
      exporter_role_arn         = aws_iam_role.telemetry_exporter.arn
      workload_log_group        = "/sentinelops/dev/workloads"
      trace_sampling_percentage = 10
    }
    error_message = "The public handoff must match the renderer and declared infrastructure inputs."
  }
}
