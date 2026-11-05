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
  description = "Per-workload IRSA role ARNs to annotate each Kubernetes service account with."
  value = {
    telemetry-reader = aws_iam_role.telemetry_reader.arn
    data-worker      = aws_iam_role.data_worker.arn
    explanation      = aws_iam_role.explanation.arn
    controller       = aws_iam_role.controller.arn
    api              = aws_iam_role.api.arn
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

output "bedrock_invoke_granted" {
  description = "False until explicit model ids are supplied; the explanation role then has no invoke permission."
  value       = length(var.bedrock_model_ids) > 0
}

output "model_activation_allowed" {
  description = "Infrastructure never authorizes model activation; H1/G3 acceptance is separate."
  value       = false
}

output "remediation_approval" {
  description = "Infrastructure never authorizes remediation; H4/G6 acceptance is separate."
  value       = false
}
