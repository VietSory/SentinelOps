# Offline only. Terraform test evaluates plans with both providers mocked.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-southeast-1a", "ap-southeast-1b", "ap-southeast-1c"] }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/software-fixture" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-southeast-1:123456789012:log-group:/software-fixture" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:ap-southeast-1:123456789012:key/11111111-2222-3333-4444-555555555555" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.ap-southeast-1.amazonaws.com/id/FIXTURE" }
  }
  mock_resource "aws_eks_cluster" {
    defaults = { identity = [{ oidc = [{ issuer = "https://oidc.eks.ap-southeast-1.amazonaws.com/id/FIXTURE" }] }] }
  }
  mock_resource "aws_eks_node_group" {
    defaults = { resources = [{ autoscaling_groups = [{ name = "eks-demo-fixture" }] }] }
  }
  mock_resource "aws_db_instance" {
    defaults = { master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:fixture" }] }
  }
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:loadbalancer/app/fixture/1111111111111111"
      dns_name = "internal-fixture.ap-southeast-1.elb.amazonaws.com"
    }
  }
  mock_resource "aws_subnet" {
    defaults = { id = "subnet-11111111111111111" }
  }
  mock_resource "aws_security_group" {
    defaults = { id = "sg-11111111111111111" }
  }
  mock_resource "aws_cloudfront_vpc_origin" {
    defaults = { id = "vo_fixture" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:targetgroup/fixture/1111111111111111" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:listener/app/fixture/1111111111111111/2222222222222222" }
  }
  mock_resource "aws_cloudfront_function" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:function/fixture" }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = { domain_name = "d123fixture.cloudfront.net" }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = { endpoint = "cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_FIXTURE" }
  }
}
mock_provider "tls" {
  override_during = plan
  mock_data "tls_certificate" {
    defaults = { certificates = [{ sha1_fingerprint = "1111111111111111111111111111111111111111" }] }
  }
}
variables {
  region              = "ap-southeast-1"
  allowed_account_ids = ["123456789012"]
  environment         = "dev"
}

run "edge_off_by_default" {
  command = plan
  assert {
    condition = (
      length(aws_lb.edge) == 0 && length(aws_cloudfront_distribution.edge) == 0 &&
      length(aws_cloudfront_vpc_origin.edge) == 0 && length(aws_lb_target_group.edge) == 0 &&
      length(aws_cognito_user_pool_client.grafana) == 0 && output.edge_domain == null &&
      output.edge_grafana_client_id == null
    )
    error_message = "Baseline must create no demo edge or Grafana identity."
  }
}

run "edge_requires_identity" {
  command = plan
  variables { edge_enabled = true }
  expect_failures = [aws_lb.edge]
}

run "private_uncached_https_edge" {
  command = plan
  variables {
    edge_enabled              = true
    operator_identity_enabled = true
  }
  assert {
    condition = (
      aws_lb.edge[0].internal &&
      aws_lb.edge[0].subnets == toset(aws_subnet.private[*].id) &&
      aws_lb.edge[0].security_groups == toset([aws_security_group.edge_alb[0].id]) &&
      aws_cloudfront_vpc_origin.edge[0].vpc_origin_endpoint_config[0].arn == aws_lb.edge[0].arn &&
      length(aws_vpc_security_group_ingress_rule.edge_bootstrap) == 0 &&
      aws_vpc_security_group_ingress_rule.edge_from_cloudfront[0].cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.edge_from_cloudfront[0].cidr_ipv6 == null
    )
    error_message = "Origin must remain private, with ingress only from the CloudFront service SG."
  }
  assert {
    # The data lookup is deliberately deferred until VPC origin creation.
    # Its exact SG reference is covered by the offline mutation guard.
    condition = one([
      for filter in data.aws_security_group.edge_cloudfront[0].filter : filter.values
      if filter.name == "group-name"
    ]) == toset(["CloudFront-VPCOrigins-Service-SG"])
    error_message = "Only the service-managed CloudFront SG may reach the origin."
  }
  assert {
    condition = (
      aws_cloudfront_distribution.edge[0].enabled &&
      aws_cloudfront_distribution.edge[0].default_cache_behavior[0].viewer_protocol_policy == "https-only" &&
      aws_cloudfront_distribution.edge[0].default_cache_behavior[0].cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&
      alltrue([for behavior in aws_cloudfront_distribution.edge[0].ordered_cache_behavior :
        behavior.viewer_protocol_policy == "https-only" &&
        behavior.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&
        behavior.origin_request_policy_id == "216adef6-5c7f-47e4-b989-5492eafa07d3" &&
        one(behavior.function_association).event_type == "viewer-request"
      ]) &&
      length(aws_cloudfront_distribution.edge[0].custom_error_response) == 11 &&
      alltrue([for error in aws_cloudfront_distribution.edge[0].custom_error_response : error.error_caching_min_ttl == 0]) &&
      toset([for behavior in aws_cloudfront_distribution.edge[0].ordered_cache_behavior : behavior.path_pattern]) == toset(["/v1/*", "/ui/*", "/grafana/*"])
    )
    error_message = "All behaviors require HTTPS, disabled caching and the path filter."
  }
  assert {
    condition = (
      aws_lb_listener.edge[0].default_action[0].type == "fixed-response" &&
      aws_lb_listener.edge[0].default_action[0].fixed_response[0].status_code == "403" &&
      aws_lb_listener_rule.edge_deny[0].priority < aws_lb_listener_rule.edge_allow["api"].priority &&
      aws_lb_listener_rule.edge_deny[0].priority < aws_lb_listener_rule.edge_allow["grafana"].priority &&
      one(aws_lb_listener_rule.edge_allow["api"].condition).path_pattern[0].values == toset(["/ui", "/ui/*", "/v1/*"]) &&
      one(aws_lb_listener_rule.edge_allow["grafana"].condition).path_pattern[0].values == toset(["/grafana", "/grafana/*"]) &&
      alltrue([for target in aws_lb_target_group.edge : target.target_type == "instance"]) &&
      alltrue([for attachment in aws_autoscaling_attachment.edge : attachment.autoscaling_group_name == "eks-demo-fixture"])
    )
    error_message = "Only the declared API/UI and Grafana paths may forward to instance/NodePort targets."
  }
  assert {
    condition = (
      aws_cognito_user_pool.operators[0].mfa_configuration == "ON" &&
      !aws_cognito_user_pool_client.operator[0].generate_secret &&
      aws_cognito_user_pool_client.operator[0].allowed_oauth_scopes == toset(["openid", "sentinelops/incidents.read", "sentinelops/remediation.approve"]) &&
      output.operator_oidc_binding.operator_scope == "sentinelops/incidents.read" &&
      output.operator_oidc_binding.approval_scope == "sentinelops/remediation.approve" &&
      aws_cognito_user_pool_client.grafana[0].generate_secret &&
      aws_cognito_user_pool_client.operator[0].callback_urls == toset(["https://d123fixture.cloudfront.net/ui/callback"]) &&
      aws_cognito_user_pool_client.grafana[0].callback_urls == toset(["https://d123fixture.cloudfront.net/grafana/login/generic_oauth"]) &&
      aws_cognito_user_pool_client.grafana[0].allowed_oauth_flows == toset(["code"]) &&
      aws_cognito_user_pool_client.grafana[0].allowed_oauth_scopes == toset(["openid", "email", "profile"])
    )
    error_message = "UI requires read/proposal-approval scopes; Grafana must retain only identity scopes, separate code/PKCE callbacks and mandatory pool MFA."
  }
}

run "bootstrap_does_not_publish" {
  command = plan
  variables {
    edge_enabled              = true
    operator_identity_enabled = true
    edge_vpc_origin_bootstrap = true
    edge_waf_enabled          = true
  }
  assert {
    condition = (
      !aws_cloudfront_distribution.edge[0].enabled &&
      length(aws_vpc_security_group_ingress_rule.edge_from_cloudfront) == 0 &&
      aws_vpc_security_group_ingress_rule.edge_bootstrap[0].prefix_list_id == data.aws_ec2_managed_prefix_list.edge_cloudfront[0].id &&
      aws_vpc_security_group_ingress_rule.edge_bootstrap[0].cidr_ipv4 == null &&
      aws_wafv2_web_acl_association.edge[0].resource_arn == aws_lb.edge[0].arn
    )
    error_message = "Bootstrap permits only CloudFront's managed prefix list and cannot publish; optional WAF attaches internally."
  }
}

run "nodeports_must_be_distinct" {
  command = plan
  variables { edge_grafana_node_port = 30080 }
  expect_failures = [var.edge_grafana_node_port]
}

run "nodeport_must_be_in_kubernetes_range" {
  command = plan
  variables { edge_api_node_port = 80 }
  expect_failures = [var.edge_api_node_port]
}
