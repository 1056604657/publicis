# SLB 模块（负载均衡）
# 内网负载均衡，挂载到后端服务，用于应用入口流量的负载分发
variable "environment" {}
variable "vpc_id" {}
variable "vswitch_id" {}

# 负载均衡实例（内网型，不用公网 IP）
resource "alicloud_slb_load_balancer" "this" {
  load_balancer_name   = "marriott-${var.environment}-slb"
  address_type         = "intranet" # 内网型，走 VPC 内部
  vswitch_id           = var.vswitch_id
  load_balancer_spec   = "slb.s1.small" # 最小规格（dev/test 够用）
  internet_charge_type = "PayByTraffic"
}

output "slb_id" {
  value = alicloud_slb_load_balancer.this.id
}

output "slb_address" {
  value = alicloud_slb_load_balancer.this.address
}
