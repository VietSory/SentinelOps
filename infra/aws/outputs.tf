# Outputs carry identifiers and references the operator needs for handoff.
# No secret value is output: the control database password lives only in Secrets
# Manager, and only its ARN is exposed here.

output "region" {
  description = "Region this environment is described in."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS API endpoint; private unless cluster_public_access was enabled."
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_public_access_enabled" {
  description = "Whether the API server is reachable from outside the VPC."
  value       = aws_eks_cluster.main.vpc_config[0].endpoint_public_access
}

output "oidc_provider_arn" {
  description = "IRSA provider ARN used by the per-workload roles."
  value       = aws_iam_openid_connect_provider.cluster.arn
}

output "workload_role_arns" {
  description = "Per-workload IRSA role ARNs; use service-account annotation or the collector's explicit STS token binding."
  value = {
    telemetry-reader   = aws_iam_role.telemetry_reader.arn
    telemetry-exporter = aws_iam_role.telemetry_exporter.arn
    data-worker        = aws_iam_role.data_worker.arn
    explanation        = aws_iam_role.explanation.arn
    controller         = aws_iam_role.controller.arn
    api                = aws_iam_role.api.arn
  }
}

output "collector_bindings" {
  description = "Public inputs for the offline collector renderer; not proof of deployment, delivery or H3 parity."
  value = {
    region                    = var.region
    environment               = var.environment
    exporter_role_arn         = aws_iam_role.telemetry_exporter.arn
    workload_log_group        = local.log_group_names.workloads
    trace_sampling_percentage = var.xray_fixed_sampling_rate * 100
  }
}

output "artifact_bucket" {
  description = "Versioned, encrypted artifact bucket for raw/features/models/incidents."
  value       = aws_s3_bucket.artifacts.bucket
}

output "control_database_endpoint" {
  description = "Private control PostgreSQL endpoint."
  value       = aws_db_instance.control.address
}

output "control_runtime_secret_arn" {
  description = "Secrets Manager ARN the workloads read for the control_writer runtime DSN (operator sets its value)."
  value       = aws_secretsmanager_secret.control_runtime.arn
}

output "control_database_secret_arn" {
  description = "Secrets Manager ARN holding the generated control database credentials."
  value       = aws_db_instance.control.master_user_secret[0].secret_arn
}

output "log_group_names" {
  description = "CloudWatch log groups with their bounded retention."
  value = {
    cluster   = aws_cloudwatch_log_group.cluster.name
    workloads = aws_cloudwatch_log_group.workloads.name
    vpc_flow  = aws_cloudwatch_log_group.vpc_flow.name
  }
}

output "operations_dashboard_name" {
  description = "Operator CloudWatch dashboard name; access and real metric/log delivery must be verified after authorized deployment."
  value       = aws_cloudwatch_dashboard.operations.dashboard_name
}

output "bedrock_invoke_granted" {
  description = "False until explicit model ids are supplied; the explanation role then has no invoke permission."
  value       = length(var.bedrock_model_ids) + length(var.bedrock_resource_arns) > 0
}

output "model_activation_allowed" {
  description = "Infrastructure never authorizes model activation; H1/G3 acceptance is separate."
  value       = false
}

output "remediation_approval" {
  description = "Infrastructure never authorizes remediation; H4/G6 acceptance is separate."
  value       = false
}

output "image_repository_urls" {
  description = "Private ECR repositories; deployment manifests pin images from these by digest."
  value       = { for name, repository in aws_ecr_repository.images : name => repository.repository_url }
}

output "edge_domain" {
  description = "Demo CloudFront domain; null when disabled. UI at /ui/, Grafana at /grafana/."
  value       = var.edge_enabled ? aws_cloudfront_distribution.edge[0].domain_name : null
}

output "edge_grafana_client_id" {
  description = "Public ID of Grafana's confidential code/PKCE client; the sensitive client secret is never output."
  value       = var.operator_identity_enabled && var.edge_enabled ? aws_cognito_user_pool_client.grafana[0].id : null
}

output "demo_alerts_topic_arn" {
  description = "SNS topic for demo alert emails; pass to render_worker_manifests --alerts-topic-arn."
  value       = aws_sns_topic.demo_alerts.arn
}
