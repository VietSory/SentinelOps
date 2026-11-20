"""Mutation tests of edge invariants; no Terraform process or AWS access."""
import re
import shutil

import pytest

from scripts import check_aws_iac as iac


@pytest.fixture
def edge_copy(tmp_path, monkeypatch):
    for path in iac.ROOT.glob('*.tf'):
        shutil.copy(path, tmp_path/path.name)
    monkeypatch.setattr(iac, 'ROOT', tmp_path)
    return tmp_path


def test_real_edge():
    assert iac.assert_edge(iac.resources())


@pytest.mark.parametrize(('old', 'new'), [
    ('internal                   = true', 'internal                   = false'),
    ('aws_subnet.private[*].id', 'aws_subnet.public[*].id'),
    ('viewer_protocol_policy     = "https-only"', 'viewer_protocol_policy     = "allow-all"'),
    ('"/ui", "/ui/*", "/v1/*"', '"/ui", "/ui/*", "/*"'),
    ('"/grafana", "/grafana/*"', '"/grafana", "/grafana/*", "/prometheus/*"'),
    ('"fixed-response"', '"forward"'),
    ('status_code  = "403"', 'status_code  = "200"'),
    ('4135ea2d-6df8-44a3-9df3-4b5a84be39ad', '658327ea-f89d-4fab-a63d-7e88639e58f6'),
    ('origin_request_policy_id   = local.edge_origin_request_policy_id', 'origin_request_policy_id   = "none"'),
    ('enabled             = !var.edge_vpc_origin_bootstrap', 'enabled             = true'),
    ('target_type = "instance"', 'target_type = "ip"'),
    ('health = "/grafana/api/health"', 'health = "/prometheus/health"'),
    ('condition = var.operator_identity_enabled', 'condition = true'),
    ('216adef6-5c7f-47e4-b989-5492eafa07d3', 'untrusted-origin-policy'),
    ('error_caching_min_ttl = 0', 'error_caching_min_ttl = 10'),
    ('"/grafana/public/plugins/jaeger/plugin.json"', '"/grafana/public/plugins/jaeger/api"'),
    ('values = ["GET", "HEAD"]', 'values = ["GET", "HEAD", "POST"]'),
    ('priority     = 1', 'priority     = 3'),
    ('value = "no-store"', 'value = "public,max-age=3600"'),
    ('data.aws_security_group.edge_cloudfront[0].id', 'aws_security_group.nodes.id'),
    ('"CloudFront-VPCOrigins-Service-SG"', '"arbitrary-sg"'),
    ('"com.amazonaws.global.cloudfront.origin-facing"', '"untrusted-prefix-list"'),
    ('return event.request;', 'return event.request; // changed security boundary'),
])
def test_edge_mutations_are_rejected(edge_copy, old, new):
    path = edge_copy/'edge.tf'
    source = path.read_text()
    # Ignore Terraform fmt alignment, while preserving the original fixture.
    pattern = re.escape(old).replace(r'\ ', r'[ \t]+')
    changed, count = re.subn(pattern, lambda _: new, source, count=1)
    assert count == 1, f'fixture drift: {old}'
    if old == 'return event.request;':
        # A changed comment alone is immaterial; insert an actual bypass.
        changed = source.replace('var uri = event.request.uri;', 'return event.request; var uri = event.request.uri;')
    path.write_text(changed)
    with pytest.raises(ValueError):
        iac.assert_edge(iac.resources())


def test_world_ingress_rejected(edge_copy):
    path = edge_copy/'edge.tf'
    with path.open('a') as stream:
        stream.write('''
resource "aws_vpc_security_group_ingress_rule" "extra_edge" {
  security_group_id = aws_security_group.edge_alb[0].id
  cidr_ipv4 = "0.0.0.0/0"
  ip_protocol = "tcp"
  from_port = 80
  to_port = 80
}
''')
    with pytest.raises(ValueError, match='ingress'):
        iac.assert_edge(iac.resources())


def test_edge_cannot_default_on(edge_copy):
    path = edge_copy/'variables.tf'
    path.write_text(re.sub(r'(variable "edge_enabled" \{[^}]*default\s*=\s*)false',
                           r'\g<1>true', path.read_text(), count=1))
    with pytest.raises(ValueError, match='disabled by default'):
        iac.assert_edge(iac.resources())


@pytest.mark.parametrize('scope', ['sentinelops/ingest', 'sentinelops/remediation.approve'])
def test_grafana_cannot_acquire_api_scope(edge_copy, scope):
    path = edge_copy/'operator_identity.tf'
    path.write_text(path.read_text().replace('["openid", "email", "profile"]',
                                           '["openid", "email", "profile", "'+scope+'"]'))
    with pytest.raises(ValueError, match='identity scopes'):
        iac.assert_operator_identity(iac.resources())


def test_operator_mfa_cannot_be_disabled(edge_copy):
    path = edge_copy/'operator_identity.tf'
    path.write_text(path.read_text().replace('mfa_configuration   = "ON"', 'mfa_configuration   = "OFF"'))
    with pytest.raises(ValueError, match='mandatory software-token MFA'):
        iac.assert_operator_identity(iac.resources())


def test_extra_distribution_is_rejected(edge_copy):
    path = edge_copy/'edge.tf'
    with path.open('a') as stream:
        stream.write('\nresource "aws_cloudfront_distribution" "extra" {\n  enabled = true\n}\n')
    with pytest.raises(ValueError, match='declared private origin'):
        iac.assert_edge(iac.resources())
