# Demo edge only. CloudFront is the sole public endpoint; no Ingress controller.
# Application authentication remains mandatory: API verifies Cognito JWTs, UI
# uses code/PKCE, Grafana uses its own generic_oauth code/PKCE client.
locals {
  edge_backends = {
    api     = { port = var.edge_api_node_port, health = "/health" }
    grafana = { port = var.edge_grafana_node_port, health = "/grafana/api/health" }
  }
  edge_paths = {
    api     = ["/ui", "/ui/*", "/v1/*"]
    grafana = ["/grafana", "/grafana/*"]
  }
  # AWS managed CachingDisabled and AllViewer. Forward Host, Authorization,
  # cookies and query strings (OAuth state/code), never cache authenticated data.
  # Managed-CachingDisabled; verified with `aws cloudfront list-cache-policies --type managed`.
  edge_cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  edge_origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
}

resource "aws_security_group" "edge_alb" {
  count       = var.edge_enabled ? 1 : 0
  name        = "${var.name_prefix}-${var.environment}-edge-alb"
  description = "Internal ALB; CloudFront VPC origin only"
  vpc_id      = aws_vpc.main.id
}

# AWS creates this SG when the first VPC origin is provisioned in this VPC.
# Bootstrap is a separate targeted phase; normal operation uses only this SG.
data "aws_security_group" "edge_cloudfront" {
  count = var.edge_enabled && !var.edge_vpc_origin_bootstrap ? 1 : 0
  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG"]
  }
  filter {
    name   = "vpc-id"
    values = [aws_vpc.main.id]
  }
  depends_on = [aws_cloudfront_vpc_origin.edge]
}

resource "aws_vpc_security_group_ingress_rule" "edge_from_cloudfront" {
  count                        = var.edge_enabled && !var.edge_vpc_origin_bootstrap ? 1 : 0
  security_group_id            = aws_security_group.edge_alb[0].id
  referenced_security_group_id = data.aws_security_group.edge_cloudfront[0].id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
}

data "aws_ec2_managed_prefix_list" "edge_cloudfront" {
  count = var.edge_enabled && var.edge_vpc_origin_bootstrap ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc_security_group_ingress_rule" "edge_bootstrap" {
  count             = var.edge_enabled && var.edge_vpc_origin_bootstrap ? 1 : 0
  security_group_id = aws_security_group.edge_alb[0].id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.edge_cloudfront[0].id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

# Managed nodes currently carry the EKS-created cluster SG, not the unused
# aws_security_group.nodes. Restrict both directions to the two NodePorts.
resource "aws_vpc_security_group_egress_rule" "edge_to_nodes" {
  for_each                     = var.edge_enabled ? local.edge_backends : {}
  security_group_id            = aws_security_group.edge_alb[0].id
  referenced_security_group_id = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_security_group_ingress_rule" "edge_node_ports" {
  for_each                     = var.edge_enabled ? local.edge_backends : {}
  security_group_id            = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  referenced_security_group_id = aws_security_group.edge_alb[0].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_lb" "edge" {
  count                      = var.edge_enabled ? 1 : 0
  name                       = "${var.name_prefix}-${var.environment}-edge"
  internal                   = true
  load_balancer_type         = "application"
  ip_address_type            = "ipv4"
  subnets                    = aws_subnet.private[*].id
  security_groups            = [aws_security_group.edge_alb[0].id]
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  lifecycle {
    precondition {
      condition     = var.operator_identity_enabled
      error_message = "The demo edge requires operator_identity_enabled and application Cognito authentication."
    }
  }
}

resource "aws_lb_target_group" "edge" {
  for_each    = var.edge_enabled ? local.edge_backends : {}
  name_prefix = each.key == "api" ? "edapi-" : "edgra-"
  port        = each.value.port
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id
  health_check {
    path    = each.value.health
    matcher = "200"
  }
  lifecycle {
    create_before_destroy = true
  }
}

# One ASG for this EKS managed node group; EKS may replace its name on a node
# group replacement. The computed name is a value, never a for_each key.
resource "aws_autoscaling_attachment" "edge" {
  for_each               = var.edge_enabled ? local.edge_backends : {}
  autoscaling_group_name = one(aws_eks_node_group.main.resources[0].autoscaling_groups).name
  lb_target_group_arn    = aws_lb_target_group.edge[each.key].arn
}

resource "aws_lb_listener" "edge" {
  count             = var.edge_enabled ? 1 : 0
  load_balancer_arn = aws_lb.edge[0].arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "edge_deny" {
  count        = var.edge_enabled ? 1 : 0
  listener_arn = aws_lb_listener.edge[0].arn
  priority     = 2
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
  condition {
    path_pattern {
      regex_values = ["^/(.*/)?(internal|docs|redoc|openapi[.]json|metrics|prometheus|jaeger)(/.*)?$"]
    }
  }
}

# Grafana's built-in Jaeger datasource plugin loads several static assets below
# this prefix. Keep only GET/HEAD plugin assets available; Jaeger APIs stay denied.
resource "aws_lb_listener_rule" "edge_allow_grafana_jaeger_module" {
  count        = var.edge_enabled ? 1 : 0
  listener_arn = aws_lb_listener.edge[0].arn
  priority     = 1
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.edge["grafana"].arn
  }
  condition {
    path_pattern {
      regex_values = ["^/grafana/public/plugins/jaeger/(module[.]js|plugin[.]json|img/[^/]+[.](svg|png))$"]
    }
  }
  condition {
    http_request_method {
      values = ["GET", "HEAD"]
    }
  }
}

resource "aws_lb_listener_rule" "edge_allow" {
  for_each     = var.edge_enabled ? local.edge_paths : {}
  listener_arn = aws_lb_listener.edge[0].arn
  priority     = each.key == "api" ? 10 : 20
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.edge[each.key].arn
  }
  condition {
    path_pattern {
      values = each.value
    }
  }
}

resource "aws_cloudfront_vpc_origin" "edge" {
  count = var.edge_enabled ? 1 : 0
  vpc_origin_endpoint_config {
    name                   = "${var.name_prefix}-${var.environment}-edge"
    arn                    = aws_lb.edge[0].arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
  # VPC origins require an attached IGW, but no public route/IP for the ALB.
  depends_on = [aws_internet_gateway.main, aws_lb_listener.edge, aws_vpc_security_group_ingress_rule.edge_bootstrap]
}

resource "aws_cloudfront_function" "edge_paths" {
  count   = var.edge_enabled ? 1 : 0
  name    = "${var.name_prefix}-${var.environment}-edge-paths"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      var uri = event.request.uri;
      var grafanaJaegerAsset = (event.request.method === 'GET' || event.request.method === 'HEAD') &&
        /^\/grafana\/public\/plugins\/jaeger\/(module\.js|plugin\.json|img\/[^/]+\.(svg|png))$/.test(uri);
      // Reject ambiguous encodings/traversal before CloudFront normalization
      // and origin routing can disagree. OAuth query parameters are untouched.
      if (/[%%\\]/.test(uri) || uri.indexOf('//') !== -1 || /(^|\/)\.{1,2}(\/|$)/.test(uri) ||
          (!grafanaJaegerAsset && /(^|\/)(internal|docs|redoc|openapi\.json|metrics|prometheus|jaeger)(\/|$)/i.test(uri)) ||
          !/^(\/ui(\/|$)|\/v1\/|\/grafana(\/|$))/.test(uri)) {
        return {statusCode: 403, statusDescription: 'Forbidden', headers: {'cache-control': {value: 'no-store'}}};
      }
      return event.request;
    }
  JS
}

resource "aws_cloudfront_response_headers_policy" "edge" {
  count = var.edge_enabled ? 1 : 0
  name  = "${var.name_prefix}-${var.environment}-edge"
  custom_headers_config {
    items {
      header   = "Cache-Control"
      value    = "no-store"
      override = true
    }
  }
  security_headers_config {
    content_type_options { override = true }
    frame_options {
      frame_option = "SAMEORIGIN"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "edge" {
  count               = var.edge_enabled ? 1 : 0
  enabled             = !var.edge_vpc_origin_bootstrap
  comment             = "SentinelOps authenticated demo UI/API/Grafana"
  is_ipv6_enabled     = true
  price_class         = "PriceClass_All"
  wait_for_deployment = true
  origin {
    domain_name = aws_lb.edge[0].dns_name
    origin_id   = "internal-alb"
    vpc_origin_config {
      vpc_origin_id = aws_cloudfront_vpc_origin.edge[0].id
    }
  }
  default_cache_behavior {
    target_origin_id           = "internal-alb"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = local.edge_cache_policy_id
    origin_request_policy_id   = local.edge_origin_request_policy_id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.edge[0].id
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.edge_paths[0].arn
    }
  }
  dynamic "ordered_cache_behavior" {
    for_each = ["/v1/*", "/ui/*", "/grafana/*"]
    content {
      path_pattern               = ordered_cache_behavior.value
      target_origin_id           = "internal-alb"
      viewer_protocol_policy     = "https-only"
      allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods             = ["GET", "HEAD"]
      cache_policy_id            = local.edge_cache_policy_id
      origin_request_policy_id   = local.edge_origin_request_policy_id
      response_headers_policy_id = aws_cloudfront_response_headers_policy.edge[0].id
      function_association {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.edge_paths[0].arn
      }
    }
  }
  # CachingDisabled alone does not disable CloudFront's default error cache.
  # Preserve the original error status/body, without sharing a cached denial.
  dynamic "custom_error_response" {
    for_each = [400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504]
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    # AWS controls the TLS security policy for its default certificate.
    # Viewer HTTPS is enforced on every behavior above.
    cloudfront_default_certificate = true
  }
  # Do not publish until the final, origin-specific ingress exists.
  depends_on = [aws_vpc_security_group_ingress_rule.edge_from_cloudfront]
}

# Regional WAF avoids a second us-east-1 provider; applies to every origin path.
resource "aws_wafv2_web_acl" "edge" {
  count = var.edge_enabled && var.edge_waf_enabled ? 1 : 0
  name  = "${var.name_prefix}-${var.environment}-edge"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "origin-rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 1000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "edge-origin-rate-limit"
      sampled_requests_enabled   = false
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "edge"
    sampled_requests_enabled   = false
  }
}

resource "aws_wafv2_web_acl_association" "edge" {
  count        = var.edge_enabled && var.edge_waf_enabled ? 1 : 0
  resource_arn = aws_lb.edge[0].arn
  web_acl_arn  = aws_wafv2_web_acl.edge[0].arn
}
