# RDS 模块（关系型数据库）
variable "environment" {}
variable "instance_class" {}
variable "db_name" {}
variable "db_user" {}
variable "db_password" {}
variable "vpc_id" {}
variable "vswitch_id" {}
variable "vpc_cidr" {}

resource "alicloud_db_instance" "this" {
  engine           = "MySQL"
  engine_version   = "8.0"
  instance_type    = var.instance_class
  instance_storage = 20
  vpc_id           = var.vpc_id
  vswitch_id       = var.vswitch_id
  instance_name    = "marriott-${var.environment}-db"
  # 安全白名单：只允许 VPC 内网访问（不能用 0.0.0.0/0，那是裸奔）
  security_ips = [var.vpc_cidr]

  # 生产环境开启高可用（多可用区）
  category = var.environment == "production" ? "HighAvailability" : "Basic"
}

resource "alicloud_db_database" "this" {
  instance_id = alicloud_db_instance.this.id
  name        = var.db_name
}

resource "alicloud_db_account" "this" {
  instance_id = alicloud_db_instance.this.id
  name        = var.db_user
  password    = var.db_password
  type        = "Normal"
}

output "endpoint" {
  value = alicloud_db_instance.this.connection_string
}
