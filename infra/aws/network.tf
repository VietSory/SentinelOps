# Private-by-default network. Workloads run in private subnets; the only public
# subnets exist to host NAT so nodes can pull images and reach AWS APIs.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)

  # /20 private subnets first, then /24 public subnets for NAT only.
  private_subnet_cidrs = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 4, index)]
  public_subnet_cidrs  = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 8, index + 240)]
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-igw" }
}

resource "aws_subnet" "private" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  # Workload subnets never auto-assign public addresses.
  map_public_ip_on_launch = false

  tags = {
    Name                              = "${var.name_prefix}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

resource "aws_subnet" "public" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  # NAT gateways need a public address; no workload is placed here.
  map_public_ip_on_launch = false

  tags = { Name = "${var.name_prefix}-public-${local.azs[count.index]}" }
}

resource "aws_eip" "nat" {
  count = var.availability_zone_count

  domain = "vpc"

  tags = { Name = "${var.name_prefix}-nat-${local.azs[count.index]}" }
}

resource "aws_nat_gateway" "main" {
  count = var.availability_zone_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  depends_on = [aws_internet_gateway.main]

  tags = { Name = "${var.name_prefix}-nat-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-public" }
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One route table per AZ so a single NAT failure does not black-hole every subnet.
resource "aws_route_table" "private" {
  count = var.availability_zone_count

  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-private-${local.azs[count.index]}" }
}

resource "aws_route" "private_default" {
  count = var.availability_zone_count

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[count.index].id
}

resource "aws_route_table_association" "private" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# S3 traffic stays on the AWS network instead of traversing NAT; this is both a
# cost control and keeps artifact reads off the public path.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = { Name = "${var.name_prefix}-s3" }
}

resource "aws_flow_log" "vpc" {
  log_destination = aws_cloudwatch_log_group.vpc_flow.arn
  iam_role_arn    = aws_iam_role.vpc_flow_log.arn
  traffic_type    = "REJECT"
  vpc_id          = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-flow-log" }
}

# Control plane data stores accept traffic only from inside the VPC.
resource "aws_security_group" "database" {
  name        = "${var.name_prefix}-control-db"
  description = "Control PostgreSQL; PostgreSQL from cluster nodes only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-control-db" }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_nodes" {
  security_group_id = aws_security_group.database.id
  description       = "PostgreSQL from EKS nodes"

  referenced_security_group_id = aws_security_group.nodes.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "nodes" {
  name        = "${var.name_prefix}-nodes"
  description = "EKS worker nodes"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-nodes" }
}

# Egress is required for image pulls and AWS APIs; ingress stays cluster-internal.
resource "aws_vpc_security_group_egress_rule" "nodes_all" {
  security_group_id = aws_security_group.nodes.id
  description       = "Outbound to AWS APIs and image registries"

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "nodes_self" {
  security_group_id = aws_security_group.nodes.id
  description       = "Node-to-node traffic within the cluster"

  referenced_security_group_id = aws_security_group.nodes.id
  ip_protocol                  = "-1"
}
