# ACK 模块（阿里云 Kubernetes 集群）
# Task 1 题目明确要求「Kubernetes集群」组件
# 用阿里云托管版 K8s（ACK Managed），Master 由阿里云托管，只付 Worker 节点费用

variable "environment" {}
variable "vpc_id" {}
variable "vswitch_id" {}

# 各环境的 Worker 节点规格和数量
variable "worker_instance_type" {
  default = "ecs.g6.large" # dev/test 用较小规格
}

variable "worker_count" {
  default = 2 # dev/test 2 个节点
}

# ACK 托管集群
resource "alicloud_cs_managed_kubernetes" "this" {
  name         = "marriott-${var.environment}"
  cluster_spec = "ack.standard" # 标准托管版

  # 网络：复用 VPC + 交换机
  vpc_id          = var.vpc_id
  pod_vswitch_ids = [var.vswitch_id]

  # Worker 节点池
  worker_vswitch_ids    = [var.vswitch_id]
  worker_instance_types = [var.worker_instance_type]
  worker_number         = var.worker_count

  # 节点登录方式（生产用密钥对，不用密码）
  key_name = "marriott-k8s-key"

  # 集群网络插件（Flannel 简单通用，生产可用 Terway）
  addons {
    name = "flannel"
  }

  # 开启监控（阿里云 ARMS，题目要求可观测性）
  enabled_cloud_monitor = true
}

output "cluster_id" {
  value = alicloud_cs_managed_kubernetes.this.id
}

output "cluster_name" {
  value = alicloud_cs_managed_kubernetes.this.name
}
