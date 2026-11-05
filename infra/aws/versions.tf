# SentinelOps AWS baseline — single region, single cluster (TECHNICAL_SPEC §14).
#
# This configuration DESCRIBES the target environment. It is not authorization to
# create anything. Region, model access, quota, budget and apply permission are
# confirmed by the owner before any apply; see docs/runbooks/AWS_BASELINE.md.
#
# There is no backend block on purpose: a shared state backend is itself a
# provisioned resource and an owner decision. Validate with
# `terraform init -backend=false`.

terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.72.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "4.1.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Refuse to act against an unintended account even if ambient credentials exist.
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = {
      Project     = "SentinelOps"
      Environment = var.environment
      ManagedBy   = "terraform"
      Component   = "control-plane"
    }
  }
}
