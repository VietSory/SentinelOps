# Artifact object store and the control PostgreSQL database (ADR-006).
# Both are private, encrypted and separated from commerce credentials.

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.name_prefix}-artifacts-${var.environment}-${data.aws_caller_identity.current.account_id}"

  tags = { Name = "${var.name_prefix}-artifacts" }
}

# Versioned prefixes plus SHA-256; a dataset or model ID is never overwritten.
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "artifacts_bucket" {
  statement {
    sid       = "DenyUnencryptedTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts_bucket.json
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  # Current versions are never expired here: a frozen dataset or model release
  # must stay reproducible. Only superseded versions age out.
  rule {
    id     = "expire-superseded-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.artifact_noncurrent_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_db_subnet_group" "control" {
  name       = "${var.name_prefix}-control"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${var.name_prefix}-control" }
}

resource "aws_secretsmanager_secret" "control_database" {
  name                    = "${var.name_prefix}/${var.environment}/control-database"
  description             = "Control PostgreSQL credentials; rotated by RDS, never committed"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7

  tags = { Name = "${var.name_prefix}-control-database" }
}

resource "aws_db_instance" "control" {
  identifier     = "${var.name_prefix}-control-${var.environment}"
  engine         = "postgres"
  engine_version = var.database_engine_version
  instance_class = var.database_instance_class

  allocated_storage     = var.database_allocated_storage_gb
  max_allocated_storage = var.database_allocated_storage_gb * 2
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.main.arn

  db_name  = "sentinelops_control"
  username = "control_bootstrap"

  # RDS generates and stores the password in Secrets Manager; no password is
  # present in this configuration or in Terraform variables.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.main.arn

  db_subnet_group_name   = aws_db_subnet_group.control.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false

  multi_az                = var.availability_zone_count >= 2
  backup_retention_period = var.database_backup_retention_days
  copy_tags_to_snapshot   = true
  deletion_protection     = var.database_deletion_protection

  # A final snapshot preserves IER revisions and audit history on teardown.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name_prefix}-control-${var.environment}-final"

  enabled_cloudwatch_logs_exports = ["postgresql"]
  auto_minor_version_upgrade      = true

  tags = { Name = "${var.name_prefix}-control" }
}
