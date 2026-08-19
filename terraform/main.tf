# Terraform 主配置
# Task 1：基础设施即代码（阿里云）
#
# 5 套环境（dev/test/perf/staging/production）用「workspace + tfvars」双机制区分：
#   1. workspace：隔离每套环境的 state 文件（互不影响，防止误操作）
#   2. tfvars：区分每套环境的配置值（规格、副本数等）
#
# 使用方式（以 dev 为例）：
#   terraform workspace new dev        # 1. 创建 dev 的 workspace（隔离 state）
#   terraform workspace select dev     # 2. 切到 dev workspace
#   terraform plan -var-file="environments/dev.tfvars"    # 3. 预览（用 dev 的配置）
#   terraform apply -var-file="environments/dev.tfvars"   # 4. 部署
#
# 5 个环境各自创建 workspace，state 天然隔离，配置用对应 tfvars 注入。

terraform {
  required_version = ">= 1.5"

  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.0"
    }
  }

  # 远程状态（阿里云 OSS 做 backend，多环境共享状态）
  backend "oss" {
    bucket = "marriott-terraform-state"
    prefix = "marriott"
  }
}

provider "alicloud" {
  region = var.region
}

# ============================================================
# 网络（VPC + 交换机，选做但建议保留，体现完整架构）
# ============================================================
module "vpc" {
  source = "./modules/vpc"

  vpc_name    = "marriott-${var.environment}"
  vpc_cidr    = var.vpc_cidr
  region      = var.region
  environment = var.environment
}

# ============================================================
# 关系型数据库 RDS（必选）
# ============================================================
module "rds" {
  source = "./modules/rds"

  environment    = var.environment
  instance_class = var.rds_instance_class
  db_name        = "app"
  db_user        = "app"
  db_password    = var.db_password
  vpc_id         = module.vpc.vpc_id
  vswitch_id     = module.vpc.vswitch_id
  vpc_cidr       = var.vpc_cidr
}

# ============================================================
# Redis（必选）
# ============================================================
module "redis" {
  source = "./modules/redis"

  environment   = var.environment
  instance_type = var.redis_instance_type
  vpc_id        = module.vpc.vpc_id
  vswitch_id    = module.vpc.vswitch_id
  vpc_cidr      = var.vpc_cidr
}

# ============================================================
# 负载均衡 SLB（应用入口流量分发）
# ============================================================
module "slb" {
  source = "./modules/slb"

  environment = var.environment
  vpc_id      = module.vpc.vpc_id
  vswitch_id  = module.vpc.vswitch_id
}

# ============================================================
# OSS 对象存储（必选，用于备份/静态资源）
# ============================================================
module "oss" {
  source = "./modules/oss"

  environment = var.environment
  bucket_name = "marriott-${var.environment}-assets"
}

# ============================================================
# 容器镜像仓库 ACR（选做）
# ============================================================
module "acr" {
  source = "./modules/acr"

  environment = var.environment
  repo_name   = "marriott"
}

# ============================================================
# Kubernetes 集群 ACK（题目明确要求）
# ============================================================
module "ack" {
  source = "./modules/ack"

  environment          = var.environment
  vpc_id               = module.vpc.vpc_id
  vswitch_id           = module.vpc.vswitch_id
  worker_instance_type = var.ack_worker_instance_type
  worker_count         = var.ack_worker_count
}
