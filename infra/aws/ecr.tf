# Private image registry for the deployment (G7). Manifests pin images by digest,
# so tags are immutable and a digest never changes meaning. Nodes already pull
# through the read-only registry policy on the node role; nothing here grants push.

locals {
  # application: Checkout/Payment/Inventory (docker/Dockerfile.service).
  # control-plane: API and health/incident workers with the trusted model runtime.
  image_repositories = toset(["application", "control-plane"])
}

resource "aws_ecr_repository" "images" {
  for_each = local.image_repositories

  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

# Untagged layers left by failed pushes expire; tagged release images are kept.
resource "aws_ecr_lifecycle_policy" "images" {
  for_each   = aws_ecr_repository.images
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
