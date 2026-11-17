# Optional operator-only provider. Configuration validation is not cloud authority.
# No user/password, producer client, client secret or application AWS role.
# Teardown: ACTIVE blocks pool deletion. A must first review/apply a separate
# change to INACTIVE while this provider remains enabled, then review destroy.
variable "operator_identity_enabled" {
  description = "Describe an optional Cognito operator provider; enabling this does not authorize apply or user enrollment."
  type        = bool
  default     = false
}

variable "operator_callback_urls" {
  description = "Exact credential-free HTTPS callbacks handled by an operator OAuth code/PKCE client, not the API's token-paste UI."
  type        = list(string)
  default     = []
  validation {
    condition = alltrue([for url in var.operator_callback_urls :
      can(regex("^https://[a-z0-9][a-z0-9.-]*(:[0-9]+)?(/[^?#[:space:]]*)?$", url)) && !strcontains(url, "@")
    ]) && length(distinct(var.operator_callback_urls)) == length(var.operator_callback_urls)
    error_message = "Operator callbacks must be distinct exact HTTPS URLs without credentials, query strings, fragments or whitespace."
  }
}

resource "aws_cognito_user_pool" "operators" {
  count               = var.operator_identity_enabled ? 1 : 0
  name                = "${var.name_prefix}-${var.environment}-operators"
  deletion_protection = "ACTIVE"
  mfa_configuration   = "ON"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  software_token_mfa_configuration {
    enabled = true
  }
  password_policy {
    minimum_length                   = 16
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 1
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }
  lifecycle {
    precondition {
      condition     = length(var.operator_callback_urls) > 0
      error_message = "An enabled operator provider requires an explicitly selected OAuth callback client."
    }
  }
}

resource "aws_cognito_resource_server" "operator_api" {
  count        = var.operator_identity_enabled ? 1 : 0
  identifier   = "sentinelops"
  name         = "SentinelOps operator reads"
  user_pool_id = aws_cognito_user_pool.operators[0].id
  scope {
    scope_name        = "incidents.read"
    scope_description = "Read incident, explanation and action observations; no writes or dispatch"
  }
}

resource "aws_cognito_user_pool_client" "operator" {
  count                                = var.operator_identity_enabled ? 1 : 0
  name                                 = "${var.name_prefix}-${var.environment}-operator"
  user_pool_id                         = aws_cognito_user_pool.operators[0].id
  generate_secret                      = false
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "sentinelops/incidents.read"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  callback_urls                        = var.operator_callback_urls
  default_redirect_uri                 = try(var.operator_callback_urls[0], null)
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 8
  auth_session_validity                = 3
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
  depends_on = [aws_cognito_resource_server.operator_api]
}

resource "aws_cognito_user_pool_domain" "operator" {
  count        = var.operator_identity_enabled ? 1 : 0
  domain       = "${var.name_prefix}-${var.environment}-${data.aws_caller_identity.current.account_id}-operators"
  user_pool_id = aws_cognito_user_pool.operators[0].id
}

output "operator_oidc_binding" {
  description = "Optional public API renderer binding; null by default. Not proof of provider deployment, enrollment or token acceptance."
  value = var.operator_identity_enabled ? {
    issuer         = "https://${aws_cognito_user_pool.operators[0].endpoint}"
    audience       = aws_cognito_user_pool_client.operator[0].id
    jwks_url       = "https://${aws_cognito_user_pool.operators[0].endpoint}/.well-known/jwks.json"
    audience_claim = "client_id"
    operator_scope = "sentinelops/incidents.read"
  } : null
}

output "operator_login_domain" {
  description = "Optional public Cognito hosted-login base URL for the external code/PKCE client; not the OIDC issuer. Null when disabled."
  value       = var.operator_identity_enabled ? "https://${aws_cognito_user_pool_domain.operator[0].domain}.auth.${var.region}.amazoncognito.com" : null
}
