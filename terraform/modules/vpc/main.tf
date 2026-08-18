# VPC 模块
variable "vpc_name" {}
variable "vpc_cidr" {}
variable "region" {}
variable "environment" {}

resource "alicloud_vpc" "this" {
  vpc_name   = var.vpc_name
  cidr_block = var.vpc_cidr
}

resource "alicloud_vswitch" "this" {
  vpc_id       = alicloud_vpc.this.id
  cidr_block   = cidrsubnet(var.vpc_cidr, 8, 1)
  zone_id      = "${var.region}-a"
  vswitch_name = "marriott-${var.environment}-vswitch"
}

output "vpc_id" {
  value = alicloud_vpc.this.id
}

output "vswitch_id" {
  value = alicloud_vswitch.this.id
}
