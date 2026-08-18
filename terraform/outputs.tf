# Terraform 输出
# 输出 RDS、Redis、OSS 的连接信息，供 K8s overlay 引用

output "rds_endpoint" {
  description = "RDS 内网地址"
  value       = module.rds.endpoint
}

output "redis_endpoint" {
  description = "Redis 内网地址"
  value       = module.redis.endpoint
}

output "oss_bucket" {
  description = "OSS 桶名"
  value       = module.oss.bucket_name
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "slb_address" {
  description = "负载均衡内网地址"
  value       = module.slb.slb_address
}
