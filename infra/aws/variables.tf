variable "region" {
  description = "Single AWS region for the whole environment (TECHNICAL_SPEC §14)."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "region must be an explicit AWS region id, for example eu-central-1."
  }
}

variable "allowed_account_ids" {
  description = "Explicit owner-confirmed account ids; the provider refuses any other account."
  type        = list(string)

  validation {
    condition     = length(var.allowed_account_ids) > 0 && alltrue([for id in var.allowed_account_ids : can(regex("^[0-9]{12}$", id))])
    error_message = "supply at least one 12-digit AWS account id so apply cannot target an unintended account."
  }
}

variable "environment" {
  description = "Deployment environment name; the MVP baseline is a single environment."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "sentinelops"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric with hyphens, 3-21 characters."
  }
}

variable "vpc_cidr" {
  description = "VPC CIDR block."
  type        = string
  default     = "10.60.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "availability_zone_count" {
  description = "Number of AZs. RDS and EKS both require at least two subnets."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 3
    error_message = "availability_zone_count must be 2 or 3; the MVP baseline is a single small cluster."
  }
}

variable "kubernetes_version" {
  description = "EKS control plane version."
  type        = string
  default     = "1.34"

  validation {
    condition     = can(regex("^1\\.(3[0-9]|[4-9][0-9])$", var.kubernetes_version))
    error_message = "kubernetes_version must be an explicit supported 1.x minor version."
  }
}

variable "node_instance_types" {
  description = "Managed node group instance types."
  type        = list(string)
  default     = ["t3.large"]

  validation {
    condition     = length(var.node_instance_types) > 0
    error_message = "supply at least one instance type."
  }
}

variable "node_desired_size" {
  description = "Desired managed node count. Keep the MVP cluster small to bound cost."
  type        = number
  default     = 2

  validation {
    condition     = var.node_desired_size >= 1 && var.node_desired_size <= 6
    error_message = "node_desired_size must be between 1 and 6 for the MVP cost envelope."
  }
}

variable "node_max_size" {
  description = "Maximum managed node count; a bounded ceiling is a cost control."
  type        = number
  default     = 4

  validation {
    condition     = var.node_max_size >= 1 && var.node_max_size <= 8
    error_message = "node_max_size must be between 1 and 8 for the MVP cost envelope."
  }
}

variable "cluster_public_access" {
  description = <<-EOT
    Whether the EKS API server is reachable from outside the VPC. The baseline is
    false: MVP operators reach the control API/UI through private ingress or
    port-forward (TECHNICAL_SPEC §14). Enabling this requires
    cluster_public_access_cidrs to be explicit and non-zero.
  EOT
  type        = bool
  default     = false
}

variable "cluster_public_access_cidrs" {
  description = "Explicit operator CIDRs when cluster_public_access is enabled. 0.0.0.0/0 is rejected."
  type        = list(string)
  default     = []

  validation {
    condition     = !contains(var.cluster_public_access_cidrs, "0.0.0.0/0")
    error_message = "an unrestricted 0.0.0.0/0 EKS public endpoint is not an accepted configuration."
  }

  # Enabling public access without naming operator CIDRs would silently expose
  # the API server to the internet.
  validation {
    condition     = !var.cluster_public_access || length(var.cluster_public_access_cidrs) > 0
    error_message = "cluster_public_access requires explicit cluster_public_access_cidrs."
  }
}

variable "pod_identity_addon_version" {
  description = "Explicit eks-pod-identity-agent addon version; unpinned addons drift between applies."
  type        = string
  default     = "v1.3.8-eksbuild.2"

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+-eksbuild\\.[0-9]+$", var.pod_identity_addon_version))
    error_message = "pod_identity_addon_version must be an explicit vX.Y.Z-eksbuild.N version."
  }
}

variable "database_engine_version" {
  description = "Explicit PostgreSQL major.minor version; the local baseline is PostgreSQL 16."
  type        = string
  default     = "16.10"

  validation {
    condition     = can(regex("^1[6-9]\\.[0-9]+$", var.database_engine_version))
    error_message = "database_engine_version must be an explicit PostgreSQL 16+ version."
  }
}

variable "database_deletion_protection" {
  description = "Deletion protection for the audit/IER store. Disable only for a deliberate teardown."
  type        = bool
  default     = true
}

variable "database_instance_class" {
  description = "RDS instance class for the control database."
  type        = string
  default     = "db.t4g.medium"
}

variable "database_allocated_storage_gb" {
  description = "RDS allocated storage in GiB."
  type        = number
  default     = 50

  validation {
    condition     = var.database_allocated_storage_gb >= 20 && var.database_allocated_storage_gb <= 500
    error_message = "database_allocated_storage_gb must be between 20 and 500 for the MVP envelope."
  }
}

variable "database_backup_retention_days" {
  description = "RDS automated backup retention. Zero would disable backups for the audit store."
  type        = number
  default     = 7

  validation {
    condition     = var.database_backup_retention_days >= 1 && var.database_backup_retention_days <= 35
    error_message = "the control database holds IER revisions and audit; retention must be 1-35 days."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention. Reports must cite evidence that is still retrievable."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731], var.log_retention_days)
    error_message = "log_retention_days must be an AWS-supported retention value and must not be unlimited."
  }
}

variable "artifact_noncurrent_retention_days" {
  description = "How long superseded artifact versions are kept in S3 before expiry."
  type        = number
  default     = 90

  validation {
    condition     = var.artifact_noncurrent_retention_days >= 30
    error_message = "keep superseded dataset/model versions at least 30 days so a release remains reproducible."
  }
}

variable "bedrock_model_ids" {
  description = <<-EOT
    Explicit Bedrock model ids the explanation workload may invoke. Empty means the
    explanation role gets no invoke permission at all, which is the correct state
    until model access is granted. Wildcards are rejected: the invocation must be
    bounded to a specific model and region (TECHNICAL_SPEC §14).
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for id in var.bedrock_model_ids : !can(regex("[*?]", id))])
    error_message = "Bedrock model ids must be explicit; a wildcard model grant is not accepted."
  }
}

variable "xray_fixed_sampling_rate" {
  description = "Explicit X-Ray sampled fraction; affects both cost and how source coverage is read."
  type        = number
  default     = 0.1

  validation {
    condition     = var.xray_fixed_sampling_rate > 0 && var.xray_fixed_sampling_rate <= 1
    error_message = "xray_fixed_sampling_rate must be greater than 0 and at most 1."
  }
}

variable "monthly_budget_usd" {
  description = "Owner-confirmed monthly budget for the cost guardrail alarm."
  type        = number
  default     = 300

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "supply the owner-confirmed monthly budget so the cost guardrail is meaningful."
  }
}

variable "budget_notification_emails" {
  description = "Addresses notified by the budget guardrail before cost accumulates."
  type        = list(string)
  default     = []
}
