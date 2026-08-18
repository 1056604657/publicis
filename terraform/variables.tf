# Terraform 变量定义
# 所有变量按环境用 tfvars 区分配置

variable "region" {
  description = "阿里云地域"
  type        = string
  default     = "cn-shanghai"
}

variable "environment" {
  description = "环境名（dev/test/perf/staging/production）"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC 网段"
  type        = string
  default     = "10.0.0.0/16"
}

variable "rds_instance_class" {
  description = "RDS 实例规格"
  type        = string
  default     = "mysql.n2.small.1"
}

variable "db_password" {
  description = "数据库密码（敏感，通过 tfvars 或环境变量传入）"
  type        = string
  sensitive   = true
}

variable "redis_instance_type" {
  description = "Redis 实例规格"
  type        = string
  default     = "redis.master.small.default"
}
