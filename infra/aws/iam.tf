# Per-workload IRSA roles (WORKSTREAM_CONTRACT §5, TECHNICAL_SPEC §14).
#
# Telemetry, explanation and remediation authority are separate roles. No role
# here is cluster-admin, none can write IAM, and none can write the commerce
# database. No static AWS access key is created or stored anywhere.

data "aws_caller_identity" "current" {}

locals {
  oidc_provider_url = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
  namespace         = "sentinelops"
}

# Trust policy binding one Kubernetes service account to one IAM role.
data "aws_iam_policy_document" "irsa" {
  for_each = toset(["telemetry-reader", "data-worker", "explanation", "controller", "api"])

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${local.namespace}:${each.key}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

# --- Telemetry reader: reads observability sources, writes nothing ------------

resource "aws_iam_role" "telemetry_reader" {
  name               = "${var.name_prefix}-telemetry-reader"
  assume_role_policy = data.aws_iam_policy_document.irsa["telemetry-reader"].json

  tags = { Workload = "telemetry-reader" }
}

data "aws_iam_policy_document" "telemetry_reader" {
  statement {
    sid    = "ReadMetricsAndTraces"
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:DescribeLogStreams",
      "xray:BatchGetTraces",
      "xray:GetTraceSummaries",
    ]
    # CloudWatch/X-Ray read APIs are not resource-scoped; the role is still
    # read-only and confined to this account by the trust policy.
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "telemetry_reader" {
  name   = "telemetry-read"
  role   = aws_iam_role.telemetry_reader.id
  policy = data.aws_iam_policy_document.telemetry_reader.json
}

# --- Data worker: reads approved models, writes feature/incident artifacts ----

resource "aws_iam_role" "data_worker" {
  name               = "${var.name_prefix}-data-worker"
  assume_role_policy = data.aws_iam_policy_document.irsa["data-worker"].json

  tags = { Workload = "data-worker" }
}

data "aws_iam_policy_document" "data_worker" {
  statement {
    sid       = "ReadApprovedModels"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.artifacts.arn}/models/*"]
  }

  statement {
    sid       = "WriteDerivedArtifacts"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.artifacts.arn}/features/*", "${aws_s3_bucket.artifacts.arn}/incidents/*"]
  }

  statement {
    sid       = "ListOwnPrefixes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*", "features/*", "incidents/*"]
    }
  }

  statement {
    sid       = "UseArtifactKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }

  statement {
    sid       = "ReadControlDatabaseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.control_database.arn]
  }
}

resource "aws_iam_role_policy" "data_worker" {
  name   = "data-worker"
  role   = aws_iam_role.data_worker.id
  policy = data.aws_iam_policy_document.data_worker.json
}

# --- Explanation: Bedrock invoke only, bounded to named models and region -----

resource "aws_iam_role" "explanation" {
  name               = "${var.name_prefix}-explanation"
  assume_role_policy = data.aws_iam_policy_document.irsa["explanation"].json

  tags = { Workload = "explanation" }
}

# Empty bedrock_model_ids yields a policy document with no statements, so the
# role carries no invoke permission until model access is actually granted.
data "aws_iam_policy_document" "explanation" {
  dynamic "statement" {
    for_each = length(var.bedrock_model_ids) > 0 ? [1] : []

    content {
      sid       = "InvokeNamedFoundationModels"
      effect    = "Allow"
      actions   = ["bedrock:InvokeModel"]
      resources = [for id in var.bedrock_model_ids : "arn:aws:bedrock:${var.region}::foundation-model/${id}"]
    }
  }
}

resource "aws_iam_role_policy" "explanation" {
  count = length(var.bedrock_model_ids) > 0 ? 1 : 0

  name   = "bedrock-invoke"
  role   = aws_iam_role.explanation.id
  policy = data.aws_iam_policy_document.explanation.json
}

# --- Controller: no AWS mutation authority at all ----------------------------
#
# Patching the approved Deployment is a Kubernetes RBAC permission, granted by
# infra/kubernetes/payment-rollback-rbac.yaml, not an AWS permission. This role
# exists only to read the evidence it decides on, so an AWS credential can never
# become remediation authority.

resource "aws_iam_role" "controller" {
  name               = "${var.name_prefix}-controller"
  assume_role_policy = data.aws_iam_policy_document.irsa["controller"].json

  tags = { Workload = "controller" }
}

data "aws_iam_policy_document" "controller" {
  statement {
    sid       = "ReadKnownGoodEvidence"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.artifacts.arn}/known-good/*"]
  }

  statement {
    sid       = "ReadImageMetadata"
    effect    = "Allow"
    actions   = ["ecr:DescribeImages", "ecr:BatchGetImage", "ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadControlDatabaseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.control_database.arn]
  }
}

resource "aws_iam_role_policy" "controller" {
  name   = "controller-read"
  role   = aws_iam_role.controller.id
  policy = data.aws_iam_policy_document.controller.json
}

# --- API/UI: reads the control database secret and incident artifacts ---------

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.irsa["api"].json

  tags = { Workload = "api" }
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadControlDatabaseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.control_database.arn]
  }

  statement {
    sid       = "ReadIncidentEvidence"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.artifacts.arn}/incidents/*"]
  }

  statement {
    sid       = "UseArtifactKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api-read"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --- Cluster and node service roles -----------------------------------------

data "aws_iam_policy_document" "eks_service" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name_prefix}-cluster"
  assume_role_policy = data.aws_iam_policy_document.eks_service.json
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

data "aws_iam_policy_document" "ec2_service" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name_prefix}-node"
  assume_role_policy = data.aws_iam_policy_document.ec2_service.json
}

# The node role carries only what kubelet needs. Application permissions come
# from IRSA so a pod cannot inherit workload authority from its host.
resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_registry" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# --- VPC flow log delivery role ---------------------------------------------

data "aws_iam_policy_document" "flow_log_service" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "vpc_flow_log" {
  name               = "${var.name_prefix}-vpc-flow-log"
  assume_role_policy = data.aws_iam_policy_document.flow_log_service.json
}

data "aws_iam_policy_document" "vpc_flow_log" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.vpc_flow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "vpc_flow_log" {
  name   = "flow-log-delivery"
  role   = aws_iam_role.vpc_flow_log.id
  policy = data.aws_iam_policy_document.vpc_flow_log.json
}
