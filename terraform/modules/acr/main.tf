# ACR 容器镜像仓库模块
variable "environment" {}
variable "repo_name" {}

resource "alicloud_cr_repository" "this" {
  namespace = "marriott"
  name      = "${var.repo_name}-${var.environment}"
  summary   = "Marriott app images (${var.environment})"
  repo_type = "PRIVATE"
}
