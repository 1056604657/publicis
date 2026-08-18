# Redis 模块
variable "environment" {}
variable "instance_type" {}
variable "vpc_id" {}
variable "vswitch_id" {}
variable "vpc_cidr" {}

resource "alicloud_kvstore_instance" "this" {
  db_instance_name = "marriott-${var.environment}-redis"
  instance_class   = var.instance_type
  engine_version   = "7.0"
  vswitch_id       = var.vswitch_id
  # 安全白名单：只允许 VPC 内网访问（不能用 0.0.0.0/0）
  security_ips  = [var.vpc_cidr]
  instance_type = "Redis"
}

output "endpoint" {
  value = alicloud_kvstore_instance.this.connection_string
}
