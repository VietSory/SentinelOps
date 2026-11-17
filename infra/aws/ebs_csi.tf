# EBS CSI driver so worker PersistentVolumeClaims (gp2/gp3) can bind. EKS 1.34 has no in-tree EBS
# provisioner; without this addon every PVC stays Pending. The driver's controller authenticates through
# EKS Pod Identity (the pod_identity addon). AmazonEBSCSIDriverPolicyV2 limits volume/snapshot changes to
# resources the driver tags; the inline policy lets the driver use only the environment KMS key, through EC2,
# so volumes from the encrypted gp3 StorageClass use that key.

data "aws_iam_policy_document" "ebs_csi_pod_identity" {
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

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name_prefix}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_pod_identity.json

  tags = { Workload = "ebs-csi-driver" }
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2"
}

data "aws_iam_policy_document" "ebs_csi_kms" {
  statement {
    sid       = "GrantsForEbsOnly"
    effect    = "Allow"
    actions   = ["kms:CreateGrant", "kms:ListGrants", "kms:RevokeGrant"]
    resources = [aws_kms_key.main.arn]

    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }

  statement {
    sid       = "UseKeyThroughEc2"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [aws_kms_key.main.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ebs_csi_kms" {
  name   = "ebs-csi-kms"
  role   = aws_iam_role.ebs_csi.id
  policy = data.aws_iam_policy_document.ebs_csi_kms.json
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name  = aws_eks_cluster.main.name
  addon_name    = "aws-ebs-csi-driver"
  addon_version = var.ebs_csi_addon_version

  pod_identity_association {
    service_account = "ebs-csi-controller-sa"
    role_arn        = aws_iam_role.ebs_csi.arn
  }

  depends_on = [aws_eks_addon.pod_identity, aws_iam_role_policy_attachment.ebs_csi, aws_iam_role_policy.ebs_csi_kms]
}
